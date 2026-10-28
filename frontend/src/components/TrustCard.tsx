/*
 src/components/TrustCard.tsx - the Trust Report card: the SAME card on every surface
 ====================================================================================
 Used by the Check page, the bank demo (compact), the explorer (look-ups) and anywhere else a report appears,
 so people learn to recognise it. Layout (Swiss grid):
   headline     one line in a block whose colour is the tone: alert = red, caution = black rule,
                record = white with a black rule, none = grey. Tones are facts' strength, never verdicts.
   facts        one row per fact: tone mark | the fact (+ detail) | source: list, date, record, official page,
                when our copy was fetched
   footer       what we looked up, what this does NOT prove, coverage and timing, the API call
*/
import type { Fact, TrustReport } from "../lib/api";

const TONE_LABEL: Record<string, string> = { alert: "Official warning", caution: "Check this", record: "Official record", none: "Nothing found" };
export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso.length <= 10 ? iso + "T00:00:00" : iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
export const fmtTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

function FactRow({ f, repeat }: { f: Fact; repeat: boolean }) {
  return (
    <li className={`fact-row t-${f.tone}`}>
      <span className="fact-mark" aria-label={TONE_LABEL[f.tone]} />
      <div className="fact-body">
        {!repeat && <p className="fact-text">{f.text}</p>}
        {f.detail && <p className="fact-detail" title={f.detail}>{f.detail}</p>}
        {Array.isArray(f.extra?.activities) && (f.extra.activities as string[]).length > 0 && (
          <p className="fact-detail">Permitted activities: {(f.extra.activities as string[]).join(", ")}</p>)}
      </div>
      {f.dataset ? (
        <dl className="fact-src">
          <div><dt>List</dt><dd>{f.dataset}</dd></div>
          {f.date && <div><dt>Published</dt><dd>{fmtDate(f.date)}</dd></div>}
          <div><dt>Record</dt><dd className="mono">{f.record_key}</dd></div>
          <div><dt>Our copy</dt><dd>{fmtTime(f.fetched_at)}{f.stale ? " (old)" : ""}</dd></div>
          {f.source_page && <div><dt>Source</dt><dd><a href={f.source_page} target="_blank" rel="noreferrer">Official page ↗</a></dd></div>}
        </dl>
      ) : <div className="fact-src muted">Searched every list</div>}
    </li>);
}

export function TrustCard({ r, compact = false }: { r: TrustReport; compact?: boolean }) {
  const facts = compact ? r.facts.filter((f) => f.tone !== "none").slice(0, 3) : r.facts;
  return (
    <article className={`trust ${compact ? "compact" : ""}`} aria-live="polite">
      <header className={`trust-head t-${r.headline.tone}`}>
        <span className="eyebrow">{TONE_LABEL[r.headline.tone]}</span>
        <h2>{r.headline.text}</h2>
      </header>
      {facts.length > 0 && <ol className="facts-list">{facts.map((f, i) => <FactRow key={i} f={f} repeat={i === 0 && f.text === r.headline.text} />)}</ol>}
      {!compact && (
        <footer className="trust-foot">
          {r.signals.length > 0 && (
            <p><span className="eyebrow">Looked up</span>{" "}
              {r.signals.map((s) => <span key={s.kind + s.value} className="tag-sig">{s.kind}: {s.shown}</span>)}</p>)}
          {r.notes.map((n) => <p key={n} className="muted">{n}</p>)}
          <p className="muted"><span className="eyebrow">Not proven</span> {r.not_proven.join(" ")}</p>
          <p className="muted small">
            {r.lists_checked} official lists checked in {r.took_ms} ms
            {r.unavailable.length > 0 && ` · unavailable now: ${r.unavailable.join(", ")}`}
            {" · "}<code>GET /api/v1/check?q={encodeURIComponent(r.query.slice(0, 60))}</code>
          </p>
        </footer>)}
    </article>);
}
