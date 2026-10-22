"""Handles "add" / "remove" requests sent from the website as GitHub issues.

The site opens a pre-filled issue whose body holds a small JSON block, e.g.
  {"action": "add", "al_id": 105778, "start": 12, "link": "https://..."}
  {"action": "remove", "ids": ["md-801513ba", "mu-123"]}
This script updates data/manual.json, rebuilds the site data and prints a short reply
(the workflow posts it as a comment and closes the issue). Only the repo owner's issues are processed.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_library as B   # reuses the MangaDex / MangaUpdates / AniList helpers

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "data" / "manual.json"
SITE_LIB = ROOT / "docs" / "data" / "library.json"


def anilist(al_id):
    q = """query($id:Int){ Media(id:$id, type:MANGA){ id title{english romaji} synonyms countryOfOrigin status chapters
           averageScore siteUrl coverImage{large} startDate{year} description(asHtml:false) genres } }"""
    r = B.S.post(B.AL, json={"query": q, "variables": {"id": int(al_id)}}, timeout=40)
    return r.json()["data"]["Media"]


def main():
    body = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        print("I couldn't find the request in this issue."); return
    req = json.loads(m.group(0))
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else {"overrides": {}, "series": []}
    manual.setdefault("series", []); manual.setdefault("overrides", {}); manual.setdefault("removed", [])
    site = {s["id"]: s for s in json.loads(SITE_LIB.read_text(encoding="utf-8"))["series"]}

    if req.get("action") == "remove":
        ids = [str(i) for i in req.get("ids", []) if re.fullmatch(r"[a-z]{1,3}-[A-Za-z0-9-]{1,60}", str(i))]
        names = [site[i]["title"] for i in ids if i in site]
        manual["removed"] = sorted(set(manual["removed"]) | set(ids))
        MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_site_data.py")], check=True, capture_output=True)
        print(f"Removed {len(ids)} series: " + ", ".join(names[:20]) + ("…" if len(names) > 20 else ""))
        return

    if req.get("action") != "add" or not str(req.get("al_id", "")).isdigit():
        print("Unknown request."); return
    al_id = int(req["al_id"])
    if any(s.get("al_id") == al_id for s in site.values()) and not any(str(al_id) == str(x) for x in manual["removed"]):
        # already tracked: just un-remove it if needed
        sid = next(k for k, s in site.items() if s.get("al_id") == al_id)
        manual["removed"] = [x for x in manual["removed"] if x != sid]
        MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_site_data.py")], check=True, capture_output=True)
        print(f"{site[sid]['title']} is already in your library."); return

    a = anilist(al_id)
    title_en = (a["title"] or {}).get("english")
    title = (a["title"] or {}).get("romaji") or title_en
    e = {"title": title, "en_title": title_en, "al_title_en": title_en, "alt": (a.get("synonyms") or [])[:10],
         "al_id": al_id, "al_url": a.get("siteUrl"), "al_score": a.get("averageScore"), "al_status": a.get("status"),
         "country": a.get("countryOfOrigin"), "cover": (a.get("coverImage") or {}).get("large"),
         "year": (a.get("startDate") or {}).get("year"), "mu_genres": (a.get("genres") or [])[:6],
         "description": re.sub(r"<[^>]+>", "", a.get("description") or "")[:600], "sources": {}}
    link = str(req.get("link") or "").strip()
    if re.match(r"https?://", link):
        host = re.sub(r"^www\.", "", link.split("/")[2])
        name = {"weebcentral.com": "weebcentral", "mgeko.cc": "mgeko", "mangadex.org": "mangadex", "comick.io": "comick"}.get(host, "link")
        e["sources"][name] = {"url": link}
    # find MangaDex + MangaUpdates entries for release checking
    for t in [title_en, title] + e["alt"][:3]:
        if not t:
            continue
        md, score = B.md_search(t)
        if md and score >= 0.88:
            e.update({"md_id": md["md_id"], "cover": e["cover"] or md.get("cover"), "lang": md.get("lang")})
            if "mangadex" not in e["sources"]:
                e["sources"]["mangadex"] = {"url": f"https://mangadex.org/title/{md['md_id']}"}
            mu = B.mu_id_from_link((md.get("links") or {}).get("mu"))
            if mu:
                e["mu_id"] = mu
            break
    if not e.get("mu_id"):
        for t in [title_en, title]:
            if t:
                sid, sc = B.mu_search(t)
                if sid and sc >= 0.88:
                    e["mu_id"] = sid; break
    if e.get("mu_id"):
        e.update({k: v for k, v in (B.mu_series(e["mu_id"]) or {}).items() if v is not None})
    latest = [B.chap_num(e.get("mu_latest")), B.chap_num(a.get("chapters"))]
    if e.get("md_id"):
        ml = B.md_latest_chapter(e["md_id"])
        e["md_latest"] = ml
        latest.append(B.chap_num((ml or {}).get("chapter")))
    latest = [x for x in latest if x]
    e["latest"] = max(latest) if latest else None
    start = B.chap_num(req.get("start")) or 0
    e["last_read"] = start
    e["progress_known"] = True
    manual["series"] = [s for s in manual["series"] if s.get("al_id") != al_id] + [e]
    MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=1), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_site_data.py")], check=True, capture_output=True)
    tracked = "New chapters will be checked every 2 hours." if (e.get("mu_id") or e.get("md_id")) else \
        "I couldn't find it on MangaUpdates or MangaDex, so it's listed but new chapters can't be checked automatically."
    print(f"Added **{title_en or title}** (latest chapter: {e['latest'] or '?'}, you're on {start}). {tracked}")


if __name__ == "__main__":
    main()
