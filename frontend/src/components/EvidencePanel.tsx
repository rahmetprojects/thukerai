/*
 src/components/EvidencePanel.tsx - "How I worked this out": the evidence behind one answer
 ===========================================================================================
 Shows exactly what the answer was built from, so a person (or a judge) can check it:
   Calculations            each deterministic calculation: what went in, what came out, the formula
   Official records        each list searched, whether it matched (exact / similar / none / unavailable),
                           the matching record and its date
   Data used               dataset, publisher, when OUR copy was fetched (snapshot), link to the source page
   Assumptions / limits    what the result takes for granted and what it cannot tell you
 Used twice: inline under a reply (phones) and as the right-hand panel (wide screens).
*/
import type { Calc, Check, Evidence } from "../lib/api";
import type { Strings } from "../i18n";
import { Calc as CalcIcon, Doc } from "./Icons";

const TOOL_NAMES: Record<string, string> = {
  savings_runway: "How long savings last", longevity_stress: "Living longer: three scenarios",
  retirement_capital_needed: "Money needed to cover the gap", emergency_fund: "Emergency buffer",
  inflation_purchasing_power: "Buying power after price rises", future_cost: "Future cost of today's spending",
  compound_growth: "Growth over time", deposit_vs_inflation: "Interest compared with inflation", fee_impact: "Cost of fees",
  return_reconcile: "Promised versus actual growth", investment_loss: "Effect of a fall in value", concentration: "Eggs in one basket",
  loan_cost: "True cost of the loan", income_stress: "Money left each month", debt_vs_savings: "Pay the debt or keep the cash",
  rate_change: "If the interest rate changes", premium_change: "Premium increase", charges_total: "Unknown charges added up",
  remittance_compare: "What your family receives", property_sale: "Selling the home", family_support: "Supporting family",
  monthly_budget: "Monthly budget", promised_return: "What the promised return really means", inflation: "Official UAE inflation (FCSC)",
};
const VERDICT: Record<string, string> = {
  warning_found: "Found on the CMA warnings list", possible_warning: "A similar name is on the CMA warnings list",
  licensed_record_found: "Found on the CMA licensed companies list", authorised_finfluencer_found: "Found on the authorised finfluencers list",
  registered_record_found: "Found on a CMA registration list", no_record_found: "No matching record found (not proof of safety)",
  could_not_check: "Could not check: lists unavailable",
};
const MATCH: Record<string, string> = { exact: "exact match", domain: "same website", normalized: "same name", probable: "very similar name",
  possible: "somewhat similar name", none: "no match" };

const label = (k: string) => k.replace(/_/g, " ").replace(/\bpct\b/, "%").replace(/^./, (c) => c.toUpperCase());
function show(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map(show).join(", ");
  if (typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${label(k)}: ${show(x)}`).join("; ");
  return String(v);
}
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) : "-");

function CalcBlock({ c }: { c: Calc }) {
  const scen = (c.outputs as { scenarios?: Record<string, unknown>[] }).scenarios;
  return (
    <div className="ev-item">
      <h4><CalcIcon /> {TOOL_NAMES[c.tool] ?? label(c.tool)}</h4>
      <dl className="kv">
        {Object.entries(c.inputs).map(([k, v]) => (<div key={"i" + k}><dt>{label(k)}</dt><dd>{show(v)}</dd></div>))}
      </dl>
      {scen ? (
        <ul className="scen">{scen.map((s, i) => (<li key={i}>{show(s)}</li>))}</ul>
      ) : (
        <dl className="kv out">
          {Object.entries(c.outputs).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => (
            <div key={"o" + k}><dt>{label(k)}</dt><dd>{show(v)}</dd></div>))}
        </dl>)}
      <p className="formula">{c.formula}</p>
    </div>
  );
}

function CheckBlock({ c }: { c: Check }) {
  const lists = c.type === "entity_check" ? c.lists ?? [] : [{ dataset_id: c.dataset_id!, name: c.name!, searched: !!c.searched,
    match: c.match ?? "none", snapshot_id: c.snapshot_id ?? null, fetched_at: c.fetched_at ?? null, candidates: c.candidates ?? [] }];
  return (
    <div className="ev-item">
      <h4><Doc /> {c.verdict ? VERDICT[c.verdict] ?? c.verdict : "Search"}{c.query?.name ? `: “${c.query.name}”` : ""}</h4>
      {(c.flags ?? []).map((f) => (<p key={f} className="flag">{f.split(":").slice(1).join(":").trim() || f}</p>))}
      <ul className="lists">
        {lists.map((l) => (
          <li key={l.dataset_id} className={l.match !== "none" ? "hit" : ""}>
            <span className="ln">{l.name}</span>
            <span className="lm">{!l.searched ? "not available" : MATCH[l.match] ?? l.match}</span>
            {l.candidates[0] && l.match !== "none" && (
              <span className="lc">{l.candidates[0].name}{l.candidates[0].date ? `, ${day(l.candidates[0].date)}` : ""}
                {l.candidates[0].record_key ? ` (${l.candidates[0].record_key.slice(0, 18)})` : ""}</span>)}
          </li>))}
      </ul>
    </div>
  );
}

export function EvidencePanel({ ev, t }: { ev: Evidence; t: Strings }) {
  const has = ev.calculations?.length || ev.checks?.length;
  return (
    <div className="evidence">
      {!has && <p className="muted">{t.noEvidence}</p>}
      {ev.calculations?.length > 0 && (<section><h3>{t.calculations}</h3>{ev.calculations.map((c, i) => <CalcBlock key={i} c={c} />)}</section>)}
      {ev.checks?.length > 0 && (<section><h3>{t.records}</h3>{ev.checks.map((c, i) => <CheckBlock key={i} c={c} />)}</section>)}
      {ev.points?.length > 0 && (<section><ul className="plain">{ev.points.map((p) => <li key={p}>{p}</li>)}</ul></section>)}
      {ev.data?.length > 0 && (
        <section><h3>{t.sources}</h3>
          <ul className="plain sources">{ev.data.map((d) => (
            <li key={d.dataset_id}><a href={d.source_page} target="_blank" rel="noreferrer">{d.name}</a>, {d.publisher}.{" "}
              {d.snapshot_id ? `Our copy: ${day(d.fetched_at)}, ${d.rows.toLocaleString("en-US")} records.` : "Not available right now."}
              {d.status === "stale" && " This copy is out of date."}</li>))}</ul>
        </section>)}
      {ev.assumptions?.length > 0 && (<section><h3>{t.assumptions}</h3><ul className="plain">{ev.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></section>)}
      {ev.limitations?.length > 0 && (<section><h3>{t.limits}</h3><ul className="plain">{ev.limitations.map((a) => <li key={a}>{a}</li>)}</ul></section>)}
      {ev.next_steps?.length > 0 && (<section><h3>{t.nextSteps}</h3><ul className="plain">{ev.next_steps.map((a) => <li key={a}>{a}</li>)}</ul></section>)}
    </div>
  );
}
