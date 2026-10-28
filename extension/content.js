/*
 * extension/content.js - the badge: one strip at the top of the page, only when there is an official fact
 * =======================================================================================================
 * What it checks:
 *   - on a social profile (instagram.com/x, tiktok.com/@x, x.com/x, youtube.com/@x, t.me/x): the handle
 *   - on any other website: the site's own domain (lookalikes of licensed firms are caught by the server)
 * What it shows (the Trust Report's top fact, with its source and date):
 *   alert    red strip    e.g. "named in a CMA warning published 22 Oct 2026"
 *   caution  grey strip   e.g. "a similar name is in a CMA warning"
 *   record   white strip  e.g. "matches a CMA-licensed company" / "authorised finfluencer, valid until ..."
 *   nothing found -> NOTHING is shown (no alert fatigue; "not found" proves nothing anyway)
 * The strip lives in a closed shadow root so the page's CSS cannot restyle or hide it.
 */
(() => {
  const SOCIAL = { "instagram.com": 1, "tiktok.com": 1, "x.com": 1, "twitter.com": 1, "youtube.com": 1, "t.me": 1,
                   "facebook.com": 1, "snapchat.com": 1, "threads.net": 1 };
  const RESERVED = new Set(["p", "reel", "reels", "explore", "stories", "watch", "shorts", "home", "search", "i",
                            "settings", "messages", "notifications", "hashtag", "status", "share", "login", "accounts"]);
  const SKIP = /(^|\.)(google\.[a-z.]+|bing\.com|duckduckgo\.com|localhost|127\.0\.0\.1)$/;
  let lastQ = "", host = null;

  function query() {
    const h = location.hostname.replace(/^(www\.|m\.)/, "");
    if (SKIP.test(h)) return "";
    if (SOCIAL[h]) {
      const seg = location.pathname.split("/").filter(Boolean)[0] || "";
      const handle = seg.replace(/^@/, "");
      return handle && !RESERVED.has(handle.toLowerCase()) ? `https://${h}/${handle}` : "";
    }
    return h;
  }

  function el(tag, style, text) {
    const e = document.createElement(tag);
    if (style) e.setAttribute("style", style);
    if (text) e.textContent = text;
    return e;
  }

  function show(report) {
    const top = report.facts && report.facts[0];
    if (!top || top.tone === "none") return hide();
    if (!host) {
      host = document.createElement("div");
      host.style.cssText = "all:initial;position:fixed;top:0;left:0;right:0;z-index:2147483647";
      document.documentElement.appendChild(host);
    }
    const root = host.shadowRoot || host.attachShadow({ mode: "closed" });
    const colors = { alert: ["#D71920", "#fff"], caution: ["#F2F2F2", "#111"], record: ["#fff", "#111"] }[top.tone];
    const bar = el("div", `font:500 14px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:${colors[0]};` +
      `color:${colors[1]};padding:10px 48px 10px 16px;border-bottom:1px solid #111;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap`);
    bar.appendChild(el("strong", "font-weight:700;letter-spacing:.02em;text-transform:uppercase;font-size:11px",
      top.tone === "alert" ? "Official warning" : top.tone === "caution" ? "Check this" : "Official record"));
    bar.appendChild(el("span", "flex:1 1 320px", top.text));
    const src = el("a", `color:inherit;font-size:12px;opacity:.85;text-decoration:underline`,
      `${top.dataset}${top.date ? " · " + new Date(top.date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""} · official page`);
    src.href = top.source_page; src.target = "_blank"; src.rel = "noopener";
    bar.appendChild(src);
    const x = el("button", "position:absolute;right:12px;top:8px;background:none;border:0;color:inherit;font-size:18px;cursor:pointer", "×");
    x.setAttribute("aria-label", "Close");
    x.onclick = hide;
    bar.appendChild(x);
    root.replaceChildren(bar);
  }

  function hide() { if (host) { host.remove(); host = null; } }

  function run() {
    const q = query();
    if (q === lastQ) return;
    lastQ = q;
    if (!q) return hide();
    chrome.runtime.sendMessage({ q }, (res) => {
      if (chrome.runtime.lastError || !res || !res.ok) return;      // server off: stay silent
      if (q === lastQ) show(res.report);
    });
  }

  run();
  setInterval(run, 1500);                                // single-page apps change the URL without reloading
})();
