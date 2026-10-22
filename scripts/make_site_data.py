"""Builds the small files the website reads (docs/data/library.json and a first releases.json)
from the full library (data/library.json). Re-run after the library is rebuilt or edited."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "data" / "library.json"
EXTRA = ROOT / "data" / "manual.json"      # hand-added series / overrides
OUT = ROOT / "docs" / "data" / "library.json"
REL = ROOT / "docs" / "data" / "releases.json"


def num(v):
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def key(e):
    if e.get("md_id"):
        return "md-" + e["md_id"][:8]
    if e.get("mu_id"):
        return "mu-" + str(e["mu_id"])
    return "x-" + re.sub(r"[^a-z0-9]+", "-", (e.get("title") or "").lower()).strip("-")[:40]


def kind(e):
    c = e.get("country") or {"ja": "JP", "ko": "KR", "zh": "CN", "zh-hk": "CN"}.get(e.get("lang") or "", "")
    return {"JP": "manga", "KR": "manhwa", "CN": "manhua", "TW": "manhua"}.get(c, "")


def status(e):
    s = (e.get("al_status") or e.get("status") or "").lower()
    if e.get("mu_completed") or s in ("finished", "completed"):
        return "finished"
    if s in ("hiatus",):
        return "hiatus"
    if s in ("cancelled",):
        return "cancelled"
    if s in ("not_yet_released",):
        return "upcoming"
    return "ongoing" if s else ""


def main():
    full = json.loads(FULL.read_text(encoding="utf-8"))["series"]
    extra = json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else {"series": [], "overrides": {}}
    out, rel_seed = [], {}
    for e in full + extra.get("series", []):
        k = e.get("id") or key(e)
        e.update(extra.get("overrides", {}).get(k, {}))
        title = e.get("al_title_en") or e.get("en_title") or e.get("title")
        src = {name: {kk: vv for kk, vv in s.items() if kk in ("url", "last_read")} for name, s in (e.get("sources") or {}).items()}
        rec = {
            "id": k, "title": title, "orig": e.get("title") if e.get("title") != title else None,
            "alt": [a for a in (e.get("alt") or [])[:8] if a and a.isascii()][:4],
            "cover": e.get("cover") or e.get("mu_cover"), "kind": kind(e), "status": status(e),
            "year": e.get("year") or e.get("mu_year"),
            "genres": (e.get("mu_genres") or e.get("tags") or [])[:6],
            "desc": (e.get("description") or "")[:400],
            "rating": {"mu": e.get("mu_rating"), "al": e.get("al_score"), "md": round(e["md_rating"], 2) if e.get("md_rating") else None},
            "md_id": e.get("md_id"), "mu_id": e.get("mu_id"), "al_id": e.get("al_id"),
            "links": {"mangadex": f"https://mangadex.org/title/{e['md_id']}" if e.get("md_id") else None,
                      "mangaupdates": e.get("mu_url"), "anilist": e.get("al_url")},
            "sources": src,
            "imported_last_read": e.get("last_read"),
            "progress_known": bool(e.get("progress_known")),
            # where progress starts: what Weeb Central says you've read, otherwise assume caught up at import
            "start": (num(e.get("last_read")) or 0) if e.get("progress_known") else (num(e.get("latest")) or 0),
        }
        out.append(rec)
        latest = num(e.get("latest"))
        if latest:
            mdl = e.get("md_latest") or {}
            exact = mdl.get("chapter") and num(mdl.get("chapter")) == latest
            rel_seed[k] = {"latest": latest, "chapter": str(latest).rstrip("0").rstrip("."), "date": mdl.get("date") if exact else None,
                           "group": mdl.get("group") if exact else None, "url": mdl.get("url") if exact else None,
                           "exact": bool(exact), "history": []}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    details = {r["id"]: {"desc": r.pop("desc"), "genres": r.pop("genres")} for r in out}   # loaded only when a series is opened
    (OUT.parent / "details.json").write_text(json.dumps(details, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    OUT.write_text(json.dumps({"series": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if REL.exists():   # keep what the release checker already found, only fill gaps
        rel = json.loads(REL.read_text(encoding="utf-8"))
        for k, v in rel_seed.items():
            cur = rel["series"].get(k)
            if not cur or (cur.get("latest") or 0) < v["latest"]:
                rel["series"][k] = {**v, "history": (cur or {}).get("history", [])}
    else:
        rel = {"checked": None, "series": rel_seed}
    REL.write_text(json.dumps(rel, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(out), "series written")


if __name__ == "__main__":
    main()
