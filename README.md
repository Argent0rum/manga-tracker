# Next Chapter

My manga / manhwa / manhua tracker: **https://argent0rum.github.io/manga-tracker/**

- **New chapters** – every 2 hours GitHub checks MangaUpdates (who released what) and MangaDex (exact chapter links) for everything in the library.
- **Read** – opens the exact chapter when there's a direct link, otherwise your page for that series (Weeb Central / mgeko / MangaDex…, order set in Settings). Opening a chapter marks it read.
- **Progress** – saved in the browser, and on AniList once connected (Settings).

## Files
- `import/sources.json` – the lists read from MangaDex, Weeb Central and mgeko.
- `data/library.json` – merged library (built by the "Build library" action).
- `data/manual.json` – hand fixes and extra series.
- `docs/` – the website; `docs/data/releases.json` is updated by the "Check releases" action.

## Adding a series
Add it to `data/manual.json` under `series` (title, `mu_id` from its MangaUpdates page, and `sources` with your link), then run `python scripts/make_site_data.py` — or just ask Claude.
