/*
 src/views/Check.tsx - the Trust Report page: paste anything, get official facts with sources
 ============================================================================================
 One box. Paste a company name, a website, an @account, a phone number or a whole forwarded message, or drop a
 screenshot (read by the AI helper if a key is set; what to look up is still chosen by code). The answer is the
 TrustCard: facts with their list, date, record and official page, never a verdict.
 Examples are real cases from the test data, so the demo shows all four tones.
*/
import { useState } from "react";
import * as api from "../lib/api";
import { TrustCard } from "../components/TrustCard";

const EXAMPLES: [string, string][] = [
  ["A website from a WhatsApp ad", "market10.net"],
  ["A lookalike of a licensed firm", "4tglobal-invest.net"],
  ["The firm's real website", "4tglobal.ae"],
  ["A finfluencer's account", "instagram.com/promoter12"],
  ["A forwarded message", "Hi dear, I am Sara from Faraz Financial Services. Guaranteed 15% a month. Visit market10.net or WhatsApp +971 50 123 4567 today only."],
];

export function Check() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<api.TrustReport | null>(null);
  const [err, setErr] = useState("");

  const run = async (t: string) => {
    if (!t.trim()) return;
    setText(t); setBusy(true); setErr("");
    try { setR(await api.check(t)); } catch { setErr("The check could not run. Is the server running?"); }
    finally { setBusy(false); }
  };
  const onFile = async (f?: File | null) => {
    if (!f) return;
    setBusy(true); setErr("");
    try { setR(await api.checkImage(f)); } catch { setErr("The screenshot could not be read."); }
    finally { setBusy(false); }
  };

  return (
    <div className="page grid">
      <section className="col-intro">
        <p className="eyebrow">Trust Report</p>
        <h1 className="display">Check before<br />you send money.</h1>
        <p className="lede">Official records from the Capital Market Authority and other official sources.
          Facts with their source and date, never a guess, and nothing anyone can buy their way into.</p>
      </section>

      <section className="col-main">
        <form className="checkbox" onSubmit={(e) => { e.preventDefault(); run(text); }}>
          <label htmlFor="q" className="eyebrow">Paste a name, website, @account, phone number or message</label>
          <textarea id="q" value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={4000}
            placeholder="e.g. market10.net" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(text); } }} />
          <div className="row">
            <button className="btn" type="submit" disabled={busy || !text.trim()}>{busy ? "Checking" : "Check"}</button>
            <label className="btn ghost file">Screenshot<input type="file" accept="image/*" onChange={(e) => onFile(e.target.files?.[0])} /></label>
          </div>
        </form>
        <ul className="examples-list">
          {EXAMPLES.map(([label, q]) => (
            <li key={label}><button className="example" onClick={() => run(q)}><span className="eyebrow">{label}</span><span className="ex-q">{q}</span></button></li>))}
        </ul>
        {err && <p className="banner bad">{err}</p>}
        {r && <TrustCard r={r} />}
      </section>
    </div>);
}
