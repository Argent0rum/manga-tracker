"""One-off (re-runnable) import: turns the lists read from MangaDex, WeebCentral and mgeko
(import/sources.json) into one merged library (data/library.json).

For every series it finds the MangaDex entry, the MangaUpdates entry and the AniList entry,
plus rating, status, cover and the latest chapter. Runs in GitHub Actions ("Build library").
"""
import difflib
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "import" / "sources.json"
OUT = ROOT / "data" / "library.json"
REPORT = ROOT / "data" / "import_report.json"
MD = "https://api.mangadex.org"
MU = "https://api.mangaupdates.com/v1"
AL = "https://graphql.anilist.co"
UA = {"User-Agent": "manga-tracker (github.com/Argent0rum/manga-tracker)"}
RATINGS = ["safe", "suggestive", "erotica", "pornographic"]

S = requests.Session()
S.headers.update(UA)
_last = {}


def throttle(key, gap):
    t = time.time() - _last.get(key, 0)
    if t < gap:
        time.sleep(gap - t)
    _last[key] = time.time()


def get(url, key, gap, **kw):
    for attempt in range(5):
        throttle(key, gap)
        try:
            r = S.get(url, timeout=40, **kw)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1)); continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1)); continue
            return r
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
    return None


def post(url, key, gap, **kw):
    for attempt in range(5):
        throttle(key, gap)
        try:
            r = S.post(url, timeout=40, **kw)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1)); continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1)); continue
            return r
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a == b:
        return 1
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) >= 12 and (b.startswith(a) or a.startswith(b)):   # truncated titles
        r = max(r, 0.92)
    return r


def first_title(d):
    if not d:
        return ""
    return d.get("en") or d.get("ja-ro") or d.get("ko-ro") or d.get("zh-ro") or next(iter(d.values()), "")


# ---------------- MangaDex
def md_parse(m):
    a = m["attributes"]
    alts = [v for t in a.get("altTitles", []) for v in t.values()]
    cover = next((r["attributes"]["fileName"] for r in m.get("relationships", []) if r["type"] == "cover_art" and r.get("attributes")), None)
    return {
        "md_id": m["id"], "title": first_title(a.get("title")), "alt": alts[:25],
        "en_title": next((t["en"] for t in a.get("altTitles", []) if "en" in t), None),
        "links": a.get("links") or {}, "status": a.get("status"), "year": a.get("year"),
        "lang": a.get("originalLanguage"), "md_last_chapter": a.get("lastChapter") or None,
        "tags": [t["attributes"]["name"].get("en") for t in a.get("tags", [])][:12],
        "description": (a.get("description") or {}).get("en", "")[:600],
        "cover": f"https://uploads.mangadex.org/covers/{m['id']}/{cover}.256.jpg" if cover else None,
        "content_rating": a.get("contentRating"),
    }


def md_by_ids(ids):
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        params = [("ids[]", x) for x in chunk] + [("limit", 100), ("includes[]", "cover_art")] + [("contentRating[]", c) for c in RATINGS]
        r = get(f"{MD}/manga", "md", 0.25, params=params)
        if r is not None and r.ok:
            for m in r.json()["data"]:
                out[m["id"]] = md_parse(m)
    return out


def md_stats(ids):
    out = {}
    for i in range(0, len(ids), 100):
        r = get(f"{MD}/statistics/manga", "md", 0.25, params=[("manga[]", x) for x in ids[i:i + 100]])
        if r is not None and r.ok:
            for k, v in r.json().get("statistics", {}).items():
                out[k] = {"md_rating": (v.get("rating") or {}).get("bayesian"), "md_follows": v.get("follows")}
    return out


def md_search(title):
    params = [("title", title), ("limit", 8), ("includes[]", "cover_art"), ("order[relevance]", "desc")] + [("contentRating[]", c) for c in RATINGS]
    r = get(f"{MD}/manga", "md", 0.25, params=params)
    if r is None or not r.ok:
        return None, 0
    best, score = None, 0
    for m in r.json()["data"]:
        p = md_parse(m)
        s = max([sim(title, p["title"])] + [sim(title, x) for x in p["alt"]])
        if s > score:
            best, score = p, s
    return best, score


def md_latest_chapter(md_id):
    params = [("translatedLanguage[]", "en"), ("order[chapter]", "desc"), ("limit", 1), ("includes[]", "scanlation_group")] + [("contentRating[]", c) for c in RATINGS]
    r = get(f"{MD}/manga/{md_id}/feed", "md", 0.25, params=params)
    if r is None or not r.ok or not r.json().get("data"):
        return None
    c = r.json()["data"][0]
    a = c["attributes"]
    grp = next((g["attributes"]["name"] for g in c.get("relationships", []) if g["type"] == "scanlation_group" and g.get("attributes")), None)
    return {"chapter": a.get("chapter"), "date": a.get("publishAt") or a.get("readableAt"), "group": grp,
            "url": a.get("externalUrl") or f"https://mangadex.org/chapter/{c['id']}"}


# ---------------- MangaUpdates
def mu_id_from_link(v):
    v = str(v or "").strip()
    if not v:
        return None
    if re.fullmatch(r"\d{1,6}", v):      # old-style id, can't be converted
        return None
    try:
        return int(v, 36)
    except ValueError:
        return None


def mu_series(sid):
    r = get(f"{MU}/series/{sid}", "mu", 1.0)
    if r is None or not r.ok:
        return None
    d = r.json()
    return {"mu_id": d.get("series_id"), "mu_url": d.get("url"), "mu_title": d.get("title"),
            "mu_rating": d.get("bayesian_rating"), "mu_latest": d.get("latest_chapter"),
            "mu_status": d.get("status"), "mu_completed": d.get("completed"), "mu_type": d.get("type"),
            "mu_year": d.get("year"), "mu_genres": [g.get("genre") for g in d.get("genres", [])][:10],
            "mu_cover": (d.get("image") or {}).get("url", {}).get("original"),
            "mu_alt": [a.get("title") for a in d.get("associated", [])][:25]}


def mu_search(title):
    r = post(f"{MU}/series/search", "mu", 1.0, json={"search": title, "perpage": 8})
    if r is None or not r.ok:
        return None, 0
    best, score = None, 0
    for res in r.json().get("results", []):
        rec = res.get("record", {})
        s = max(sim(title, rec.get("title")), sim(title, res.get("hit_title")))
        if s > score:
            best, score = rec.get("series_id"), s
    return best, score


# ---------------- AniList
def al_media(ids):
    out = {}
    ids = [int(i) for i in ids if str(i).isdigit()]
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        q = "query {" + " ".join(f'm{x}: Media(id: {x}, type: MANGA) {{ id averageScore status chapters countryOfOrigin siteUrl title {{ english romaji }} }}' for x in chunk) + "}"
        throttle("al", 1.5)
        try:
            r = S.post(AL, json={"query": q}, timeout=40)
            data = (r.json() or {}).get("data") or {}
        except Exception:
            data = {}
        for v in data.values():
            if v:
                out[v["id"]] = {"al_id": v["id"], "al_score": v.get("averageScore"), "al_status": v.get("status"),
                                "al_chapters": v.get("chapters"), "al_url": v.get("siteUrl"), "country": v.get("countryOfOrigin"),
                                "al_title_en": (v.get("title") or {}).get("english")}
    return out


def al_search(title):
    q = "query($s:String){ Page(perPage:5){ media(search:$s, type:MANGA){ id title { english romaji native } synonyms } } }"
    throttle("al", 1.5)
    try:
        r = S.post(AL, json={"query": q, "variables": {"s": title}}, timeout=40)
        media = r.json()["data"]["Page"]["media"]
    except Exception:
        return None
    best, score = None, 0
    for m in media:
        names = [m["title"].get("english"), m["title"].get("romaji")] + (m.get("synonyms") or [])
        s = max(sim(title, n) for n in names if n) if any(names) else 0
        if s > score:
            best, score = m["id"], s
    return best if score >= 0.88 else None


# ---------------- helpers for the source lists
SLUG_JUNK = re.compile(r"-(mg\d*|mx\d*|g|c|s|young|asd|kenasd|dieasd|sop|gnn|li|okxdz|frjzo|\d{3,5}|[a-z]{1,2}\d{1,3}[a-z]*|manhwa|manhwa-\d+)$")


def slug_title(slug):
    s = slug
    for _ in range(2):
        s = SLUG_JUNK.sub("", s)
    return s.replace("-", " ").strip()


def chap_num(v):
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def main():
    src = json.loads(SRC.read_text(encoding="utf-8"))
    report = {"unmatched": [], "low_confidence": []}
    lib = {}   # key -> entry

    # 1) MangaDex library
    md_ids = [x["md_id"] for x in src["mangadex"]]
    md = md_by_ids(md_ids)
    for mid in md_ids:
        e = md.get(mid)
        if not e:
            report["unmatched"].append({"source": "mangadex", "md_id": mid}); continue
        e["sources"] = {"mangadex": {"url": f"https://mangadex.org/title/{mid}", "shelf": "reading"}}
        lib["md:" + mid] = e
    print(f"MangaDex: {len(md)}/{len(md_ids)}")

    names = {}   # normalised title -> key

    def index(k):
        e = lib[k]
        for n in [e.get("title"), e.get("en_title")] + list(e.get("alt", [])):
            n = norm(n)
            if n and n not in names:
                names[n] = k

    for k in list(lib):
        index(k)

    def lookup(title):
        n = norm(title)
        if n in names:
            return names[n]
        close = difflib.get_close_matches(n, list(names), n=1, cutoff=0.9)
        return names[close[0]] if close else None

    def attach(title, source_key, payload):
        k0 = lookup(title)
        if k0:
            lib[k0]["sources"][source_key] = payload
            return
        found, score = md_search(title)
        if found and score >= 0.82:
            k = "md:" + found["md_id"]
            if k in lib:
                lib[k]["sources"][source_key] = payload
            else:
                found["sources"] = {source_key: payload}
                lib[k] = found
                index(k)
            if score < 0.9:
                report["low_confidence"].append({"source": source_key, "title": title, "matched": found["title"], "score": round(score, 2)})
            return
        mu, mscore = mu_search(title)
        if mu and mscore >= 0.85:
            k = f"mu:{mu}"
            lib.setdefault(k, {"title": title, "alt": [], "links": {}, "mu_id": mu, "sources": {}})["sources"][source_key] = payload
            index(k)
            return
        k = "x:" + norm(title)
        lib.setdefault(k, {"title": title, "alt": [], "links": {}, "sources": {}})["sources"][source_key] = payload
        index(k)
        report["unmatched"].append({"source": source_key, "title": title})

    # 2) WeebCentral (has reading progress)
    for w in src["weebcentral"]:
        attach(w["title"], "weebcentral", {"url": f"https://weebcentral.com/series/{w['wc_id']}", "latest": w["latest"],
                                          "last_read": None if w["last_read"] in ("None", "?") else w["last_read"]})
    print("WeebCentral done")

    # 3) mgeko
    for g in src["mgeko"]:
        t = slug_title(g["slug"])
        if re.fullmatch(r"manga [a-z0-9]+", t) or len(t) < 3:
            lib.setdefault("mg:" + g["slug"], {"title": g["slug"], "alt": [], "links": {}, "sources": {}})["sources"]["mgeko"] = {"url": f"https://www.mgeko.cc/manga/{g['slug']}/", "latest": g["latest"]}
            report["unmatched"].append({"source": "mgeko", "title": g["slug"]}); continue
        attach(t, "mgeko", {"url": f"https://www.mgeko.cc/manga/{g['slug']}/", "latest": g["latest"]})
    print("mgeko done")

    # 4) enrich: MangaDex stats + latest English chapter, MangaUpdates, AniList
    mids = [e["md_id"] for e in lib.values() if e.get("md_id")]
    stats = md_stats(mids)
    al_ids = set()
    for k, e in lib.items():
        if e.get("md_id"):
            e.update(stats.get(e["md_id"], {}))
            e["md_latest"] = md_latest_chapter(e["md_id"])
        links = e.get("links") or {}
        if not e.get("mu_id"):
            e["mu_id"] = mu_id_from_link(links.get("mu"))
        if not e.get("mu_id"):
            sid, sc = mu_search(e.get("en_title") or e["title"])
            if sid and sc >= 0.88:
                e["mu_id"] = sid
        if e.get("mu_id"):
            e.update({k2: v for k2, v in (mu_series(e["mu_id"]) or {}).items() if v is not None})
        if str(links.get("al", "")).isdigit():
            e["al_id"] = int(links["al"])
        if not e.get("al_id"):
            e["al_id"] = al_search(e.get("en_title") or e["title"])
        if e.get("al_id"):
            al_ids.add(e["al_id"])
    al = al_media(sorted(al_ids))
    for e in lib.values():
        if e.get("al_id") in al:
            e.update(al[e["al_id"]])

    # 5) derived fields: latest known chapter, progress, how far behind
    for e in lib.values():
        cands = [chap_num(e.get("mu_latest")), chap_num((e.get("md_latest") or {}).get("chapter")), chap_num(e.get("md_last_chapter"))]
        cands += [chap_num(s.get("latest")) for s in e["sources"].values()]
        cands = [c for c in cands if c]
        e["latest"] = max(cands) if cands else None
        reads = [chap_num(s.get("last_read")) for s in e["sources"].values() if s.get("last_read")]
        e["last_read"] = max(reads) if reads else None
        e["progress_known"] = "weebcentral" in e["sources"]
        if e["progress_known"] and e["latest"]:
            e["behind"] = round(e["latest"] - (e["last_read"] or 0), 1)
        e.pop("links", None)

    OUT.parent.mkdir(exist_ok=True)
    entries = sorted(lib.values(), key=lambda x: norm(x.get("title")))
    OUT.write_text(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "series": entries}, ensure_ascii=False, indent=1), encoding="utf-8")
    report["counts"] = {"series": len(entries), "with_mu": sum(1 for e in entries if e.get("mu_id")), "with_al": sum(1 for e in entries if e.get("al_id")),
                        "with_md": sum(1 for e in entries if e.get("md_id"))}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
