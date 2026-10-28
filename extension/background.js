/*
 * extension/background.js - the only part of the extension that talks to the Check API
 * ===================================================================================
 * The content script sends {q}; this worker calls GET <server>/api/v1/check?q=... and answers with the Trust
 * Report. Running the request here (not in the page) keeps it out of the page's reach and avoids mixed-content
 * blocking (http://127.0.0.1 from an https page). Answers are cached for 10 minutes per query, so browsing does
 * not repeat lookups. Only the website's host name or the social handle is sent, never the page content.
 */
const TTL_MS = 10 * 60 * 1000;
const cache = new Map();

async function server() {
  const { server } = await chrome.storage.sync.get({ server: "http://127.0.0.1:8000" });
  return server.replace(/\/$/, "");
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || typeof msg.q !== "string" || !msg.q) return false;
  const hit = cache.get(msg.q);
  if (hit && Date.now() - hit.at < TTL_MS) { reply({ ok: true, report: hit.report }); return false; }
  (async () => {
    try {
      const r = await fetch(`${await server()}/api/v1/check?q=${encodeURIComponent(msg.q)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const report = await r.json();
      cache.set(msg.q, { at: Date.now(), report });
      reply({ ok: true, report });
    } catch (e) {
      reply({ ok: false, error: String(e) });            // server not running: stay silent on the page
    }
  })();
  return true;                                            // keep the channel open for the async reply
});
