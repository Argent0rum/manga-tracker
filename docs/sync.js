/* Next Chapter — "Sync from this site" bookmarklet.
   Runs on MangaDex, mgeko or Weeb Central while you're logged in, reads how far you've got in every
   series you follow there, then gives you a button that opens the tracker with the results.
   Nothing is sent anywhere else: the data travels in the tracker link itself (#sync=…).
   No innerHTML on purpose — MangaDex enforces Trusted Types. */
(async () => {
  const TRACKER = "https://argent0rum.github.io/manga-tracker/";
  const host = location.hostname.replace(/^www\./, "");
  if (document.getElementById("nc-sync")) return;

  const box = document.createElement("div");
  box.id = "nc-sync";
  box.style.cssText = "position:fixed;z-index:2147483647;right:16px;bottom:16px;width:min(340px,calc(100vw - 32px));background:#11131A;color:#ECEEF4;" +
    "font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;padding:14px 16px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.45);border:2px solid #FFB547;text-align:left";
  const head = document.createElement("div");
  head.textContent = "Next Chapter sync";
  head.style.cssText = "font-weight:800;letter-spacing:.04em;text-transform:uppercase;font-size:12px;color:#FFB547;margin-bottom:6px";
  const msg = document.createElement("div");
  const bar = document.createElement("div");
  bar.style.cssText = "height:5px;background:#2A2F3D;border-radius:9px;overflow:hidden;margin-top:10px";
  const fill = document.createElement("div");
  fill.style.cssText = "height:100%;width:0;background:#FFB547;transition:width .2s";
  bar.appendChild(fill);
  const acts = document.createElement("div");
  acts.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap";
  const cancel = document.createElement("button");
  cancel.textContent = "Close";
  cancel.style.cssText = "background:none;border:1px solid #2A2F3D;color:#9AA0B2;border-radius:8px;padding:7px 10px;cursor:pointer;font:inherit";
  cancel.onclick = () => { stopped = true; box.remove(); };
  acts.appendChild(cancel);
  box.append(head, msg, bar, acts);
  document.body.appendChild(box);
  let stopped = false;
  const say = t => { msg.textContent = t; };
  const progress = (a, b) => { fill.style.width = (b ? Math.round(a / b * 100) : 0) + "%"; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const num = v => { const m = String(v ?? "").match(/\d+(?:\.\d+)?/); return m ? +m[0] : null; };
  const parse = html => new DOMParser().parseFromString(html, "text/html");
  const chunks = (a, n) => { const out = []; for (let i = 0; i < a.length; i += n) out.push(a.slice(i, i + n)); return out; };
  async function pool(list, n, fn) {
    let i = 0;
    await Promise.all(Array.from({ length: n }, async () => { while (i < list.length && !stopped) { const k = i++; await fn(list[k], k); } }));
  }

  try {
    let site, label, items = [];

    if (host === "mangadex.org") {
      site = "mangadex"; label = "MangaDex";
      const key = Object.keys(localStorage).find(k => k.startsWith("oidc.user:"));
      const auth = key ? JSON.parse(localStorage.getItem(key) || "null") : null;
      if (!auth || !auth.access_token) throw new Error("Log in to MangaDex first, then click the bookmark again.");
      if (auth.expires_at && auth.expires_at * 1000 < Date.now()) throw new Error("Your MangaDex login needs refreshing — reload this page and click the bookmark again.");
      const H = { Authorization: "Bearer " + auth.access_token };
      const RATINGS = "contentRating[]=safe&contentRating[]=suggestive&contentRating[]=erotica&contentRating[]=pornographic";
      const api = async p => {
        for (let t = 0; t < 4; t++) {
          const r = await fetch("https://api.mangadex.org" + p, { headers: H });
          if (r.status === 401) throw new Error("Your MangaDex login needs refreshing — reload this page and click the bookmark again.");
          if (r.status === 429) { await sleep(2000 * (t + 1)); continue; }
          return r.json();
        }
        throw new Error("MangaDex is rate-limiting — wait a minute and try again.");
      };
      say("Reading your MangaDex library…");
      const statuses = (await api("/manga/status")).statuses || {};
      const ids = Object.keys(statuses);
      if (!ids.length) throw new Error("No followed series found on MangaDex.");
      const read = {};
      for (const c of chunks(ids, 100)) { const r = await api("/manga/read?grouped=true&" + c.map(x => "ids[]=" + x).join("&")); if (r.data && !Array.isArray(r.data)) Object.assign(read, r.data); }
      const chs = [...new Set(Object.values(read).flat())], cnum = {};
      let done = 0;
      for (const c of chunks(chs, 100)) {
        if (stopped) return;
        const r = await api(`/chapter?limit=100&includeUnavailable=1&${RATINGS}&` + c.map(x => "ids[]=" + x).join("&"));
        for (const ch of r.data || []) cnum[ch.id] = ch.attributes.chapter;
        done += c.length; say(`Reading chapter markers… ${done}/${chs.length}`); progress(done, chs.length + ids.length);
        await sleep(220);
      }
      const titles = {};
      for (const c of chunks(ids, 100)) {
        const r = await api(`/manga?limit=100&${RATINGS}&` + c.map(x => "ids[]=" + x).join("&"));
        for (const m of r.data || []) { const t = m.attributes.title || {}; titles[m.id] = t.en || (m.attributes.altTitles || []).map(a => a.en).find(Boolean) || Object.values(t)[0] || ""; }
        await sleep(220);
      }
      items = ids.map(id => {
        const rs = read[id] || [];
        const ns = rs.map(c => parseFloat(cnum[c])).filter(x => !isNaN(x));
        // chapters that were read but have since been deleted can't be numbered: count them instead
        const v = ns.length ? Math.max(...ns) : rs.length;
        return [id, v, titles[id] || "", statuses[id]];
      });
    }

    else if (host.includes("mgeko")) {
      site = "mgeko"; label = "mgeko";
      say("Reading your mgeko bookmarks…");
      const found = new Map();
      for (let p = 1; p <= 80 && !stopped; p++) {
        const d = parse(await fetch("/portal/bookmark/?page=" + p, { credentials: "include" }).then(r => r.text()));
        let added = 0;
        d.querySelectorAll("li.novel-item").forEach(li => {
          const a = li.querySelector('a[href^="/manga/"]'); if (!a) return;
          const slug = a.getAttribute("href").split("/")[2];
          if (slug && !found.has(slug)) { found.set(slug, ((li.querySelector(".novel-title") || {}).textContent || slug).trim()); added++; }
        });
        if (!added) break;
        say(`Reading your mgeko bookmarks… ${found.size}`);
      }
      if (!found.size) throw new Error("No bookmarks found — are you logged in to mgeko?");
      const list = [...found]; let done = 0;
      await pool(list, 6, async ([slug, title]) => {
        let v = null;
        try {
          const d = parse(await fetch("/manga/" + slug + "/", { credentials: "include" }).then(r => r.text()));
          const nav = d.querySelector("nav.links");
          const t = nav ? nav.textContent.replace(/\s+/g, " ").trim() : "";
          const m = t.match(/^(Continue|Read)\s+Chapter\s+([\d.]+)/i);
          if (m) v = /^read/i.test(m[1]) ? 0 : (parseFloat(m[2]) || 0);   // "Read chapter 1" = not started
        } catch (e) {}
        items.push([slug, v, title]);
        done++; say(`Checking where you are on mgeko… ${done}/${list.length}`); progress(done, list.length);
      });
      if (stopped) return;
    }

    else if (host === "weebcentral.com") {
      site = "weebcentral"; label = "Weeb Central";
      say("Reading your Weeb Central subscriptions…");
      const seen = new Set();
      for (let off = 0; off < 5000 && !stopped;) {
        const d = parse(await fetch(`/users/me/subscriptions/data?limit=10&offset=${off}&display_mode=Full%20Display`, { credentials: "include" }).then(r => r.text()));
        const arts = [...d.querySelectorAll("article")];
        if (!arts.length) break;
        for (const a of arts) {
          const link = a.querySelector('a[href*="/series/"]');
          const id = ((link && link.getAttribute("href").match(/series\/([A-Z0-9]+)/)) || [])[1];
          if (!id || seen.has(id)) continue;
          seen.add(id);
          const txt = a.textContent.replace(/\s+/g, " ");
          const lr = (txt.match(/Last Read:\s*(.+?)\s*(?:Status:|$)/) || [])[1];
          const title = ((a.querySelector("strong, h2, h3, .text-ellipsis") || link || {}).textContent || "").trim();
          items.push([id, lr == null || /none/i.test(lr) ? 0 : num(lr), title]);
        }
        off += arts.length;
        say(`Reading your Weeb Central subscriptions… ${seen.size}`);
      }
      if (!items.length) throw new Error("No subscriptions found — are you logged in to Weeb Central?");
    }

    else {
      say("Open MangaDex, mgeko or Weeb Central (logged in), then click this bookmark there.");
      progress(0, 1);
      return;
    }

    progress(1, 1);
    const payload = { v: 1, site, at: new Date().toISOString(), items };
    const known = items.filter(x => x[1] != null).length;
    say(`Found ${items.length} series on ${label}${known < items.length ? ` (${known} with a reading position)` : ""}. Send them to Next Chapter to update your progress.`);
    const go = document.createElement("a");
    go.href = TRACKER + "#sync=" + encodeURIComponent(JSON.stringify(payload));
    go.target = "_blank"; go.rel = "noopener";
    go.textContent = "Send to Next Chapter →";
    go.style.cssText = "background:#FFB547;color:#1A1305;font-weight:800;text-decoration:none;border-radius:8px;padding:8px 12px";
    go.onclick = () => setTimeout(() => box.remove(), 400);
    acts.insertBefore(go, cancel);
  } catch (e) {
    say(e.message || String(e));
    fill.style.background = "#FF6B5B"; progress(1, 1);
  }
})();
