"""Looks for new chapters of everything in the library and records them in docs/data/releases.json.

Sources (both are public APIs, no login needed):
  * MangaUpdates "today's releases" list: who released which chapter (scanlation groups and official).
  * MangaDex chapter feed: exact chapter links for chapters hosted or linked on MangaDex.
Runs every two hours in GitHub Actions ("Check releases").
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "docs" / "data" / "library.json"
REL = ROOT / "docs" / "data" / "releases.json"
GROUPS = ROOT / "data" / "groups.json"
MD = "https://api.mangadex.org"
MU = "https://api.mangaupdates.com/v1"
RATINGS = ["safe", "suggestive", "erotica", "pornographic"]
S = requests.Session()
S.headers["User-Agent"] = "manga-tracker (github.com/Argent0rum/manga-tracker)"


def req(method, url, **kw):
    for attempt in range(4):
        try:
            r = S.request(method, url, timeout=40, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(8 * (attempt + 1)); continue
            return r
        except requests.RequestException:
            time.sleep(8 * (attempt + 1))
    return None


def num(v):
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_release(state, key, rel):
    """Keep the newest chapter per series plus a short history."""
    s = state.setdefault(key, {"history": []})
    n = num(rel.get("chapter"))
    if n is None:
        return False
    seen = {(h.get("chapter"), h.get("group")) for h in s["history"]}
    if (rel.get("chapter"), rel.get("group")) in seen:
        # upgrade the link if we now have an exact one
        for h in s["history"]:
            if h.get("chapter") == rel.get("chapter") and h.get("group") == rel.get("group") and rel.get("exact") and not h.get("exact"):
                h.update(rel)
        return False
    s["history"].insert(0, rel)
    s["history"] = sorted(s["history"], key=lambda h: (num(h.get("chapter")) or 0, h.get("date") or ""), reverse=True)[:12]
    top = s["history"][0]
    s.update({"latest": num(top["chapter"]), "chapter": top["chapter"], "date": top.get("date"), "group": top.get("group"),
              "url": top.get("url"), "exact": top.get("exact", False)})
    return True


def group_site(gid, cache):
    if not gid:
        return None
    k = str(gid)
    if k in cache:
        return cache[k]
    r = req("GET", f"{MU}/groups/{gid}")
    site = None
    if r is not None and r.ok:
        soc = r.json().get("social") or {}
        site = soc.get("site") or soc.get("discord") or soc.get("twitter") or r.json().get("url")
    cache[k] = site
    time.sleep(1)
    return site


def main():
    lib = json.loads(LIB.read_text(encoding="utf-8"))["series"]
    rel = json.loads(REL.read_text(encoding="utf-8")) if REL.exists() else {"checked": None, "series": {}}
    state = rel["series"]
    groups = json.loads(GROUPS.read_text(encoding="utf-8")) if GROUPS.exists() else {}
    by_mu = {s["mu_id"]: s["id"] for s in lib if s.get("mu_id")}
    by_md = {s["md_id"]: s["id"] for s in lib if s.get("md_id")}
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=30)
    if rel.get("checked"):
        since = min(since, datetime.strptime(rel["checked"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=6))
    new = 0

    # 1) MangaUpdates: today's releases (all of MU), keep the ones in the library
    for page in range(1, 40):
        r = req("GET", f"{MU}/releases/days", params={"page": page, "include_metadata": "true"})
        if r is None or not r.ok:
            break
        d = r.json()
        results = d.get("results", [])
        for res in results:
            rec = res.get("record", {})
            sid = (((res.get("metadata") or {}).get("series") or {}).get("series_id"))
            if sid not in by_mu:
                continue
            g = (rec.get("groups") or [{}])[0]
            url = group_site(g.get("group_id"), groups)
            if add_release(state, by_mu[sid], {"chapter": rec.get("chapter"), "volume": rec.get("volume"), "group": g.get("name"),
                                              "date": (rec.get("time_added") or {}).get("as_rfc3339") or rec.get("release_date"),
                                              "url": url, "exact": False, "via": "mangaupdates"}):
                new += 1
        if len(results) < (d.get("per_page") or 40):
            break
        time.sleep(1)

    # 2) MangaDex: English chapters since the last check, 100 series per request
    ids = list(by_md)
    for i in range(0, len(ids), 100):
        params = [("manga[]", x) for x in ids[i:i + 100]] + [("translatedLanguage[]", "en"), ("order[readableAt]", "desc"), ("limit", 100),
                  ("readableAtSince", since.strftime("%Y-%m-%dT%H:%M:%S")), ("includes[]", "scanlation_group")] + [("contentRating[]", c) for c in RATINGS]
        r = req("GET", f"{MD}/chapter", params=params)
        if r is None or not r.ok:
            continue
        for c in r.json().get("data", []):
            a = c["attributes"]
            mid = next((x["id"] for x in c.get("relationships", []) if x["type"] == "manga"), None)
            grp = next((x["attributes"]["name"] for x in c.get("relationships", []) if x["type"] == "scanlation_group" and x.get("attributes")), None)
            if mid in by_md and add_release(state, by_md[mid], {"chapter": a.get("chapter"), "volume": a.get("volume"), "group": grp or "MangaDex",
                                                                 "date": a.get("readableAt") or a.get("publishAt"),
                                                                 "url": a.get("externalUrl") or f"https://mangadex.org/chapter/{c['id']}",
                                                                 "exact": True, "via": "mangadex"}):
                new += 1
        time.sleep(0.3)

    rel["checked"] = iso(now)
    REL.write_text(json.dumps(rel, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    GROUPS.parent.mkdir(exist_ok=True)
    GROUPS.write_text(json.dumps(groups, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{new} new releases recorded")


if __name__ == "__main__":
    sys.exit(main())
