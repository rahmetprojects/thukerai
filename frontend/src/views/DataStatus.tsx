/*
 src/views/DataStatus.tsx - the "Data" page: what was fetched, what worked, what failed, and why
 ===============================================================================================
 Reads /api/data/status and /api/data/events (api/app.py). For every official dataset it shows:
   - the outcome of its LAST fetch attempt: Updated / No change / Came back empty / Failed / Format changed / Skipped
   - how many records the assistant is using right now, and when that copy was fetched
   - the error and notes in plain words, and when it will be refreshed
 Below: the datasets on the CMA portal that are deliberately NOT fetched, and the full event log of the
 newest run (every request, page, row count and validation decision), filterable by dataset.
 "Refresh now" starts a background run (POST /api/data/refresh); the page re-reads every few seconds while it runs.
*/
import { useCallback, useEffect, useState } from "react";
import * as api from "../lib/api";
import { Refresh } from "../components/Icons";

const STATUS: Record<string, [string, string]> = {
  SUCCESS: ["Updated", "ok"], UNCHANGED: ["No change", "ok"], EMPTY: ["Came back empty", "warn"],
  FAILED: ["Failed", "bad"], SCHEMA_DRIFT: ["Format changed", "bad"], SKIPPED: ["Skipped", "muted"],
};
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "never");
const notesOf = (n: unknown): string[] => (Array.isArray(n) ? n : typeof n === "string" && n.startsWith("[") ? JSON.parse(n) : []);

export function DataStatus() {
  const [st, setSt] = useState<api.DataStatus | null>(null);
  const [events, setEvents] = useState<api.IngestEvent[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([api.dataStatus(), api.dataEvents(filter || undefined, 600)]);
      setSt(s); setEvents(e.events); setRunId(e.run_id); setErr("");
    } catch { setErr("The status could not be loaded. Is the server running?"); }
  }, [filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!st?.refreshing) return;
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [st?.refreshing, load]);

  const refresh = async () => {
    setMsg("");
    try { const r = await api.refreshData(); setMsg(r.state === "started" ? "Refresh started. This page updates by itself." : `Refresh: ${r.state}.`); load(); }
    catch { setMsg("The refresh could not be started."); }
  };

  const counts = st?.latest_run?.counts ?? {};
  const groups = ["all"];
  return (
    <div className="data-page">
      <header className="data-head">
        <p className="lede small">What was fetched, what came back empty, what failed, and why. Every request is logged.</p>
        <button className="btn" onClick={refresh} disabled={st?.refreshing}><Refresh /> {st?.refreshing ? "Refreshing" : "Refresh now"}</button>
      </header>
      {err && <p className="banner bad">{err}</p>}
      {msg && <p className="banner">{msg}</p>}
      {st && !st.live_data && <p className="banner warn">Live data is switched off (SG_LIVE_DATA=0): only saved copies are used.</p>}
      {st?.scheduler_error && <p className="banner warn">{st.scheduler_error}</p>}

      {st?.latest_run && (
        <p className="run-line">
          Last run {when(st.latest_run.started_at)}, took {st.latest_run.seconds} s:{" "}
          {Object.entries(counts).filter(([, v]) => v).map(([k, v]) => `${v} ${STATUS[k]?.[0].toLowerCase() ?? k}`).join(", ")}.
        </p>)}

      {groups.map((g) => (
        <section key={g} className="ds-group">
          {st?.datasets.map((d) => {
            const s = d.last_status ? STATUS[d.last_status] : ["Never fetched", "muted"];
            const notes = notesOf(d.last_run?.notes);
            return (
              <article key={d.id} className={`ds ${s[1]}`}>
                <div className="ds-top">
                  <div><h3>{d.official_name}</h3><p className="ds-id">{d.publisher}, dataset id {d.id}</p></div>
                  <span className={`chip-status ${s[1]}`}>{s[0]}</span>
                </div>
                <dl className="ds-facts">
                  <div><dt>Records in use</dt><dd>{d.snapshot ? d.snapshot.row_count.toLocaleString("en-US") : "none: not available to the assistant"}</dd></div>
                  <div><dt>Our copy fetched</dt><dd>{when(d.snapshot?.created_at)}</dd></div>
                  <div><dt>Last checked OK</dt><dd>{when(d.last_success_at)}</dd></div>
                  <div><dt>Last attempt</dt><dd>{when(d.last_attempt_at)}{d.last_run ? `, ${d.last_run.pages} pages, ${d.last_run.requests} requests, ${(d.last_run.bytes / 1024).toFixed(0)} KB` : ""}</dd></div>
                </dl>
                {d.last_run?.reason && <p className="ds-reason">{d.last_run.reason}</p>}
                {notes.length > 0 && <ul className="ds-notes">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
                <p className="ds-src"><a href={d.source_page} target="_blank" rel="noreferrer">Source page</a>, refreshed every {d.refresh_hours} hours</p>
              </article>);
          })}
        </section>))}

      {st && st.not_fetched.length > 0 && (
        <section className="ds-group">
          <h2>On the portal but not fetched</h2>
          <ul className="nf">{st.not_fetched.map((n) => <li key={n.name}><b>{n.name}.</b> {n.reason}</li>)}</ul>
        </section>)}

      <section className="ds-group">
        <h2>Event log of the latest run</h2>
        <p className="muted">Run {runId ?? "none yet"}. Every request, page and decision, oldest first. Also saved in logs/ingest/.</p>
        <label className="filter">Show:{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">all datasets</option>
            {st?.datasets.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
          </select>
        </label>
        <ol className="events">
          {events.map((e, i) => {
            const extra = Object.entries(e).filter(([k]) => !["t", "run", "dataset", "stage", "level", "msg"].includes(k))
              .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ");
            return (
              <li key={i} className={`ev-${e.level}`}>
                <span className="et">{e.t.slice(11, 19)}</span>
                <span className="ed">{e.dataset}</span>
                <span className="es">{e.stage}</span>
                <span className="em">{e.msg}{extra && <small>{extra}</small>}</span>
              </li>);
          })}
        </ol>
      </section>
    </div>
  );
}
