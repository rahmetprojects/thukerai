/*
 src/components/AnswerCard.tsx - one served answer: headline, facts, what it means, explain offers, sources
 =========================================================================================================
 Renders every card type from kitchen/composer.py. Reading order is fixed, top to bottom:
   1. eyebrow + headline              what the official data says, with citation numbers [1]
   2. the body                        facts / rows / a number / a calculation / an explanation / a question
   3. WHAT THIS MEANS                 the explainer's plain line (cited to the glossary, not to a record)
   4. explain chips                   "Explain: passported fund" - the jargon this answer used
   5. follow-ups                      next questions; CSV and "as code" for data answers
   6. SOURCES                         numbered; records link to the official page and say when we collected them
 A citation number scrolls to (and highlights) its source. Colour = meaning only: red = an official warning.
*/
import { Fragment, useState } from "react";
import type { AssistantCard, Citation, Cited } from "../lib/assistant";

const EYEBROW: Record<string, string> = {
  fact: "Checked against the official lists", table: "From the official data", number: "From the official data",
  datasets: "Datasets", calculation: "Calculation", explanation: "Explanation", ask: "One question",
  proposal: "Add an official source", cannot_answer: "No answer in the data", welcome: "", safety: "Safety first",
};
const TONE_WORD: Record<string, string> = { alert: "Official warning", caution: "Similar name", record: "Official record", none: "Nothing found" };

const fmtDate = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return isNaN(+d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
};

export function Cites({ ns, card, onCite }: { ns: number[]; card: string; onCite: (n: number) => void }) {
  if (!ns?.length) return null;
  return (<span className="cites">{[...new Set(ns)].map((n) => (
    <a key={n} href={`#src-${card}-${n}`} onClick={(e) => { e.preventDefault(); onCite(n); }} aria-label={`source ${n}`}>{n}</a>))}</span>);
}

export function AnswerCard({ c, onAsk, onApprove, busy }: {
  c: AssistantCard; onAsk: (text: string) => void; busy: boolean;
  onApprove?: (discovery: string, proposal: string, name: string) => Promise<string>;
}) {
  const [lit, setLit] = useState<number | null>(null);
  const [code, setCode] = useState(false);
  const [approved, setApproved] = useState<Record<string, string>>({});
  const cite = (n: number) => {
    setLit(n);
    document.getElementById(`src-${c.id}-${n}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setTimeout(() => setLit(null), 2200);
  };
  const S = ({ s }: { s: Cited }) => (<>{s.text}<Cites ns={s.cites} card={c.id} onCite={cite} /></>);
  const tone = c.tone === "neutral" ? "" : `t-${c.tone}`;
  const eyebrow = c.type === "fact" ? TONE_WORD[c.tone] ?? EYEBROW.fact : EYEBROW[c.type];

  return (
    <article className={`acard k-${c.type} ${tone}`} aria-live="polite">
      <header className="acard-head">
        {eyebrow && <p className="eyebrow">{eyebrow}{c.planner === "ai" ? " · query planned by AI, checked by rules" : ""}</p>}
        <h2><S s={{ text: c.headline, cites: c.headline_cites }} /></h2>
      </header>

      {/* ---------------------------------------------------------------- body */}
      {c.type === "number" && <p className="bignum">{c.number?.toLocaleString("en")}<span> records</span></p>}

      {!!c.facts?.length && (
        <ul className="alist">
          {c.facts.map((f, i) => (
            <li key={i} className={`t-${f.tone}`}>
              <span className="mark" />
              <div>{(f.text !== c.headline || c.facts!.length > 1) && <p className="strong"><S s={f} /></p>}
                {f.detail && <p className="muted small">{f.detail}</p>}</div>
            </li>))}
        </ul>)}

      {!!c.sentences?.length && c.type !== "safety" && (
        <div className="prose">{c.sentences.map((s, i) => (<p key={i}><S s={s} /></p>))}</div>)}

      {c.type === "safety" && (
        <ol className="safety-steps">{c.sentences.map((s, i) => (<li key={i}>{s.text}</li>))}</ol>)}

      {c.type === "calculation" && !!c.calcs?.length && (
        <dl className="calc">
          {c.calcs.slice(0, 2).map((k, i) => (
            <Fragment key={i}>
              <dt>{k.tool.replace(/_/g, " ")}</dt>
              <dd><code>{k.formula}</code></dd>
            </Fragment>))}
          {!!c.assumptions?.length && (<><dt>Assumes</dt><dd>{c.assumptions.slice(0, 3).join("; ")}</dd></>)}
        </dl>)}

      {!!c.noted?.length && (
        <p className="noted">{c.noted.map((n, i) => (<span key={i} className="tag-sig">{n.label}: {n.display}</span>))}</p>)}

      {c.type === "ask" && c.ask && !!c.ask.quick_replies?.length && (
        <div className="chips">{c.ask.quick_replies.map((q) => (
          <button key={q} className="chipbtn" disabled={busy} onClick={() => onAsk(q)}>{q}</button>))}</div>)}

      {!!c.rows?.length && (
        <div className="scroll-x">
          <table className="records">
            <thead><tr><th>Name</th><th>Date</th><th>Status / type</th><th>Source</th></tr></thead>
            <tbody>{c.rows.slice(0, 10).map((r) => (
              <tr key={r.record_key}>
                <td>{r.name}{!!r.domains?.length && <div className="muted small">{r.domains.slice(0, 2).join(", ")}</div>}</td>
                <td className="nowrap">{fmtDate(r.date)}</td>
                <td>{r.status || r.category || ""}</td>
                <td><Cites ns={r.cites} card={c.id} onCite={cite} /></td>
              </tr>))}</tbody>
          </table>
          {c.rows.length > 10 && <p className="muted small">{c.rows.length - 10} more in the download.</p>}
        </div>)}

      {c.type === "datasets" && !!c.datasets?.length && (
        <ul className="dslist">{c.datasets.map((d) => (
          <li key={d.id ?? d.dataset_id}>
            <p className="strong">{d.name}<Cites ns={d.cites} card={c.id} onCite={cite} /></p>
            {d.description && <p className="muted small">{d.description}</p>}
            <p className="small">{d.rows.toLocaleString("en")} records{d.matched ? ` · ${d.matched} match` : ""} · <a href={d.source_page} target="_blank" rel="noreferrer">official page ↗</a></p>
          </li>))}</ul>)}

      {c.type === "explanation" && !!c.explain?.datasets.length && (
        <div className="where">
          <p className="eyebrow">Where this appears in the official data</p>
          <ul className="dslist">{c.explain.datasets.map((d) => (
            <li key={d.dataset_id}>
              <p className="strong">{d.name}<Cites ns={d.cites} card={c.id} onCite={cite} /></p>
              <p className="small">{d.rows.toLocaleString("en")} records, collected {fmtDate(d.fetched_at)} · <a href={d.source_page} target="_blank" rel="noreferrer">official page ↗</a></p>
            </li>))}</ul>
        </div>)}

      {c.type === "proposal" && c.proposal && (
        <div className="where">
          <ol className="ladder small">{c.proposal.steps.map((s) => (
            <li key={s.rung} className={`r-${s.result}`}><span>{s.rung}</span><span>{s.name}</span><span>{s.result}</span><span className="muted">{s.detail}</span></li>))}</ol>
          {c.proposal.proposals.map((p) => (
            <div key={p.id} className="proposal">
              <p className="strong">{p.name} <span className="muted">· {p.publisher} · {p.rows} rows · columns: {p.columns.slice(0, 6).join(", ")}</span></p>
              <p className="small">Name column: <b>{p.field_map.name}</b>{p.problems.length ? ` · ${p.problems.join("; ")}` : ""}</p>
              {approved[p.id] ? <p className="banner">{approved[p.id]}</p> :
                <button className="btn" disabled={busy || !onApprove}
                        onClick={async () => { const msg = await onApprove!(c.proposal!.discovery, p.id, p.name); setApproved((a) => ({ ...a, [p.id]: msg })); }}>
                  Approve and load</button>}
            </div>))}
        </div>)}

      {/* ---------------------------------------------------------------- what it means + explain offers */}
      {c.meaning && (
        <div className="meaning"><p className="eyebrow">What this means</p><p><S s={c.meaning} /></p></div>)}
      {!!c.offers?.length && (
        <div className="offers">
          <span className="eyebrow">Explain</span>
          {c.offers.map((o) => (<button key={o.term} className="chipbtn" disabled={busy} onClick={() => onAsk(`Explain ${o.label}`)}>{o.label}</button>))}
        </div>)}
      {!!c.not_proven?.length && c.tone === "record" && (<p className="muted small notproven">{c.not_proven[0]}</p>)}
      {c.trimmed && <p className="muted small">Part of this answer was removed because it could not be traced to the data.</p>}

      {/* ---------------------------------------------------------------- follow-ups */}
      {!!c.followups?.length && c.type !== "ask" && (
        <div className="chips follow">{c.followups.map((q) => {
          if (q === "Download as CSV" && c.api) return (<a key={q} className="chipbtn" href={`${c.api.url}&format=csv`}>Download CSV</a>);
          if (q === "Show the query as code" && c.api) return (<button key={q} className="chipbtn" onClick={() => setCode(!code)}>{code ? "Hide code" : "As code"}</button>);
          return (<button key={q} className="chipbtn" disabled={busy} onClick={() => onAsk(q)}>{q}</button>);
        })}</div>)}
      {code && c.api && (
        <pre className="codeblock">{`GET ${c.api.url}\n\n${c.api.curl ?? ""}\n\nMCP: ${JSON.stringify(c.api.mcp ?? {}, null, 1)}`}</pre>)}

      {/* ---------------------------------------------------------------- sources */}
      {!!c.citations?.length && (
        <ol className="sources">{c.citations.map((s) => (<Source key={s.n} s={s} card={c.id} lit={lit === s.n} />))}</ol>)}
    </article>);
}

function Source({ s, card, lit }: { s: Citation; card: string; lit: boolean }) {
  const kind = s.kind === "glossary" ? "Explanation · not an official record" : s.kind === "calculation" ? "Calculator"
    : s.kind === "search" ? "Search" : s.kind === "dataset" ? "Dataset" : "Record";
  return (
    <li id={`src-${card}-${s.n}`} className={`src k-${s.kind}${lit ? " lit" : ""}`}>
      <span className="src-n">{s.n}</span>
      <div>
        <p><span className="src-kind">{kind}</span> {s.label.length > 140 ? s.label.slice(0, 140) + "…" : s.label}</p>
        <p className="muted">
          {[s.kind === "record" ? s.dataset : null, s.date ? `dated ${fmtDate(s.date)}` : null,
            s.fetched_at ? `collected ${fmtDate(s.fetched_at)}` : null, s.kind === "record" ? null : s.detail].filter(Boolean).join(" · ")}
          {s.source_page && <> · <a href={s.source_page} target="_blank" rel="noreferrer">official page ↗</a></>}
        </p>
      </div>
    </li>);
}
