/*
 src/views/Explore.tsx - "Under the hood": the data layer CMA would own
 ======================================================================
 Four tabs:
   Ask         a question in plain words -> the plan shown as chips (what was understood), the answer, the
               datasets used, the records with their sources, and the SAME question as code: API URL, curl,
               MCP tool call, CSV download. Not a chatbot: a query you can see and reuse.
   Catalogue   every dataset: plain description (English and Arabic), fields explained, live state, profile.
   Add source  the onboarding agent: paste an official URL, watch the detection ladder, review the proposed
               config (column meanings can be changed), approve -> fetched, validated, searchable at once.
   Health      the ingest status page (views/DataStatus.tsx): what was fetched, what failed, and why.
*/
import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { TrustCard, fmtDate, fmtTime } from "../components/TrustCard";
import { DataStatus } from "./DataStatus";

type Tab = "ask" | "catalogue" | "onboard" | "health";
const QUESTIONS = ["Which firms were warned against this month?", "How many warnings were published in 2026?",
  "Licensed companies allowed to do introduction", "Passported funds from ADGM", "Is Faraz Financial Services licensed?",
  "What data do you have on funds?", "Housing inflation 2025", "What changed?"];

function Ask() {
  const [q, setQ] = useState("");
  const [r, setR] = useState<api.AskResult | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (text: string) => {
    if (!text.trim()) return;
    setQ(text); setBusy(true);
    try { setR(await api.ask(text)); } finally { setBusy(false); }
  };
  const detailText = (d: Record<string, unknown>) => Object.entries(d).map(([k, v]) =>
    `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? (v as unknown[]).map((x) => (typeof x === "object" ? (x as { title?: string }).title ?? "" : String(x))).join(", ") : String(v)}`).join(" · ");
  return (
    <>
      <form className="askbar" onSubmit={(e) => { e.preventDefault(); run(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask for data in plain words" maxLength={500} aria-label="Question" />
        <button className="btn" disabled={busy || q.trim().length < 2}>{busy ? "Finding" : "Find"}</button>
      </form>
      <div className="chips">{QUESTIONS.map((x) => <button key={x} className="chipbtn" onClick={() => run(x)}>{x}</button>)}</div>
      {r && (
        <div className="ask-result">
          <div className="reading">
            <span className="eyebrow">Understood as</span>
            {r.reading.map((x) => <span key={x} className="readchip">{x}</span>)}
            <span className="muted small">{r.plan.planner === "ai" ? "planned by the AI helper, checked by code" : "planned by rules"} · {r.took_ms} ms</span>
          </div>
          <p className="answer">{r.answer}</p>
          {r.report && <TrustCard r={r.report} />}
          {r.records.length > 0 && (
            <table className="records">
              <thead><tr><th>Name</th><th>Date</th><th>List</th><th>Details</th><th>Source</th></tr></thead>
              <tbody>{r.records.map((x) => (
                <tr key={x.dataset_id + x.record_key}>
                  <td><b>{x.name || x.category}</b>{x.name_ar && <span className="ar" dir="rtl">{x.name_ar}</span>}</td>
                  <td className="nowrap">{fmtDate(x.date)}</td>
                  <td>{x.dataset}</td>
                  <td className="small">{detailText(x.details)}</td>
                  <td className="nowrap small"><a href={x.source_page} target="_blank" rel="noreferrer">Official page ↗</a><br /><span className="muted">copy {fmtTime(x.fetched_at)}</span></td>
                </tr>))}</tbody>
            </table>)}
          {r.changes && r.changes.length > 0 && (
            <ul className="plainlist">{r.changes.map((c) => <li key={c.dataset_id}><b>{c.dataset_id}</b>: {c.compared ? `${c.added} added, ${c.removed} removed since the previous copy` : c.note}</li>)}</ul>)}
          {r.datasets.length > 0 && (
            <div className="ds-used">
              <span className="eyebrow">Datasets</span>
              {r.datasets.map((d) => (
                <div key={d.id} className="ds-line">
                  <b>{d.name}</b> <span className="muted">{d.matched != null ? `${d.matched} match · ` : ""}{d.rows} rows · copy {fmtTime(d.fetched_at)}</span>{" "}
                  {d.description && <p className="small">{d.description}</p>}
                  <a href={d.source_page} target="_blank" rel="noreferrer">Official page ↗</a> · <a href={d.api.csv}>CSV</a>
                </div>))}
            </div>)}
          <div className="as-code">
            <span className="eyebrow">The same question as code</span>
            <code className="codeblock">{r.api.curl}</code>
            <code className="codeblock">{JSON.stringify(r.api.mcp)}</code>
            <p><a href={r.api.url} target="_blank" rel="noreferrer">Open as JSON ↗</a> · <a href={r.api.url + "&format=csv"}>Download CSV</a></p>
          </div>
        </div>)}
    </>);
}

function Catalogue() {
  const [cards, setCards] = useState<api.Card[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api.catalog().then((c) => setCards(c.datasets)).catch(() => setCards([])); }, []);
  return (
    <>
      <p className="lede small">Every dataset with its meaning in plain words, its fields, and the state of our copy.
        Also as a standard open-data catalogue: <a href="/api/v1/catalog.jsonld" target="_blank" rel="noreferrer">DCAT JSON-LD ↗</a></p>
      <div className="cards">
        {cards.map((c) => (
          <article key={c.id} className={`dcard s-${c.state.status}`}>
            <p className="eyebrow">{c.publisher}</p>
            <h3>{c.name}</h3>
            <p>{c.description}</p>
            {c.description_ar && <p className="ar" dir="rtl">{c.description_ar}</p>}
            <dl className="kvs small">
              <div><dt>Rows</dt><dd>{c.state.rows.toLocaleString("en-US")}</dd></div>
              <div><dt>Our copy</dt><dd>{fmtTime(c.state.fetched_at) || "none yet"}</dd></div>
              <div><dt>Status</dt><dd>{c.state.status}</dd></div>
              {c.profile.date_from && <div><dt>Dates</dt><dd>{fmtDate(c.profile.date_from)} to {fmtDate(c.profile.date_to)}</dd></div>}
            </dl>
            <button className="linkish" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "Hide fields" : "Fields"}</button>
            {open === c.id && (
              <table className="records small"><tbody>
                {c.fields.map((f) => <tr key={f.name}><td className="mono">{f.name}</td><td>{f.definition}</td><td className="nowrap">{c.profile.fill?.[f.name] != null ? `${Math.round((c.profile.fill[f.name] ?? 0) * 100)}% filled` : ""}</td></tr>)}
              </tbody></table>)}
            <p className="small"><a href={c.source_page} target="_blank" rel="noreferrer">Official page ↗</a> · <a href={c.api.records} target="_blank" rel="noreferrer">API</a> · <a href={c.api.csv}>CSV</a></p>
          </article>))}
      </div>
    </>);
}

const ROLES = ["name", "name_ar", "date", "status", "category", "website", "key"];

function Onboard() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [d, setD] = useState<api.Discovery | null>(null);
  const [maps, setMaps] = useState<Record<string, Record<string, string>>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, string>>({});
  const run = async () => {
    setBusy(true); setD(null); setResult({});
    try {
      const out = await api.discover(url.trim());
      setD(out);
      setMaps(Object.fromEntries(out.proposals.map((p) => [p.id, { ...p.field_map }])));
      setNames(Object.fromEntries(out.proposals.map((p) => [p.id, p.name])));
    } catch { setResult({ _: "Discovery failed. Is the server running?" }); }
    finally { setBusy(false); }
  };
  const ok = async (p: api.Proposal) => {
    setResult((x) => ({ ...x, [p.id]: "Fetching and validating..." }));
    try {
      const r = await api.approve(d!.discovery, p.id, { field_map: maps[p.id], official_name: names[p.id] });
      setResult((x) => ({ ...x, [p.id]: r.ok ? `Live: ${r.rows} records published as “${r.dataset_id}”. It is now in Check, Ask, the API and MCP.` : `Not published: ${r.error || r.reason}` }));
    } catch { setResult((x) => ({ ...x, [p.id]: "Approval failed." })); }
  };
  return (
    <>
      <p className="lede small">Paste the address of an official data page. The agent finds the table behind it, proposes
        how to read it, and runs a trial. You approve; plain code then refreshes it on schedule, with the same checks as the
        CMA lists. Only official domains are accepted.</p>
      <form className="askbar" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://bayanat.ae/dataset/..." aria-label="Official URL" />
        <button className="btn" disabled={busy || url.trim().length < 8}>{busy ? "Looking" : "Discover"}</button>
      </form>
      {result._ && <p className="banner bad">{result._}</p>}
      {d && (
        <>
          <ol className="ladder">{d.steps.map((s) => (
            <li key={s.rung + s.name} className={`r-${s.result}`}><span className="mono">{s.rung || "!"}</span><b>{s.name}</b><span>{s.result}</span><span className="small">{s.detail}</span></li>))}
          </ol>
          <p className="muted small">{d.requests} requests · {d.took_ms} ms · {d.proposals.length} proposal(s)</p>
          {d.proposals.map((p) => (
            <article key={p.id} className="proposal">
              <div className="row between"><input className="title-input" value={names[p.id] ?? ""} onChange={(e) => setNames({ ...names, [p.id]: e.target.value })} aria-label="Dataset name" />
                <span className="muted small">rung {p.rung} · {p.rows} rows · usable {Math.round(p.score * 100)}%</span></div>
              <p className="small mono">{String(p.request.url)}</p>
              <div className="roles">{ROLES.map((role) => (
                <label key={role}><span className="eyebrow">{role}</span>
                  <select value={maps[p.id]?.[role] ?? ""} onChange={(e) => setMaps({ ...maps, [p.id]: { ...maps[p.id], [role]: e.target.value } })}>
                    <option value="">(none)</option>{p.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select></label>))}
              </div>
              <div className="scroll-x"><table className="records small">
                <thead><tr>{p.columns.slice(0, 8).map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{p.sample.map((row, i) => <tr key={i}>{p.columns.slice(0, 8).map((c) => <td key={c}>{row[c]}</td>)}</tr>)}</tbody>
              </table></div>
              {p.problems.map((x) => <p key={x} className="banner warn small">{x}</p>)}
              <div className="row"><button className="btn" onClick={() => ok(p)}>Approve and fetch</button>
                {result[p.id] && <span className="small">{result[p.id]}</span>}</div>
            </article>))}
          {d.documents.length > 0 && <p className="muted small">{d.documents.length} PDF document(s) found: candidates for document search, not tables.</p>}
        </>)}
    </>);
}

export function Explore() {
  const [tab, setTab] = useState<Tab>("ask");
  return (
    <div className="page">
      <section className="head-row">
        <div>
          <p className="eyebrow">Under the hood</p>
          <h1 className="display small">One trusted layer of official data.</h1>
        </div>
        <nav className="tabs" aria-label="Sections">
          {([["ask", "Ask"], ["catalogue", "Catalogue"], ["onboard", "Add a source"], ["health", "Health"]] as [Tab, string][]).map(([k, label]) => (
            <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)} aria-pressed={tab === k}>{label}</button>))}
        </nav>
      </section>
      {tab === "ask" && <Ask />}
      {tab === "catalogue" && <Catalogue />}
      {tab === "onboard" && <Onboard />}
      {tab === "health" && <DataStatus />}
    </div>);
}
