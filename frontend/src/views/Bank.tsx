/*
 src/views/Bank.tsx - DEMO: how a bank's payment screen would use the Check API
 ==============================================================================
 Clearly labelled as a demo (no real bank, no real money). Left: a phone-sized transfer form. When the person
 presses "Review", the app calls GET /api/v1/check?q=<payee name> - one call, no personal data, a few
 milliseconds - and:
   alert / caution  -> the transfer PAUSES on a full-screen stop, showing the same Trust Report card; the default
                       button is "Cancel transfer", continuing needs a second, deliberate confirmation
   record / none    -> the normal confirmation, with one quiet line saying the payee was checked
 Right: what the bank sends and receives, so judges see the integration is one line of code.
*/
import { useState } from "react";
import * as api from "../lib/api";
import { TrustCard } from "../components/TrustCard";

type Stage = "form" | "checking" | "stop" | "confirm" | "sent" | "cancelled";

export function Bank() {
  const [payee, setPayee] = useState("Faraz Financial Services");
  const [iban, setIban] = useState("AE07 0331 2345 6789 0123 456");
  const [amount, setAmount] = useState("50,000");
  const [stage, setStage] = useState<Stage>("form");
  const [r, setR] = useState<api.TrustReport | null>(null);
  const [sure, setSure] = useState(false);

  const review = async (e: React.FormEvent) => {
    e.preventDefault();
    setStage("checking"); setSure(false);
    try {
      const rep = await api.check(payee);
      setR(rep);
      setStage(rep.headline.tone === "alert" || rep.headline.tone === "caution" ? "stop" : "confirm");
    } catch { setR(null); setStage("confirm"); }                  // check unavailable: the bank's normal flow
  };
  const reset = () => { setStage("form"); setR(null); setSure(false); };

  return (
    <div className="page grid">
      <section className="col-intro">
        <p className="eyebrow">Demo · a bank's payment screen</p>
        <h1 className="display">The check<br />comes to the money.</h1>
        <p className="lede">Nobody visits a regulator's website before a transfer. So the bank asks for them, in the
          moment that matters, with one API call.</p>
        <div className="integration">
          <p className="eyebrow">What the bank sends</p>
          <code className="codeblock">GET /api/v1/check?q={encodeURIComponent(payee)}</code>
          <p className="eyebrow">What it gets back</p>
          <code className="codeblock">{r ? `tone: ${r.headline.tone}\n${r.headline.text}\n${r.facts.filter((f) => f.dataset).length} cited fact(s) · ${r.took_ms} ms` : "Press Review to see the answer"}</code>
          <p className="muted small">No customer data is sent: only the payee's name. Nothing is stored.</p>
        </div>
      </section>

      <section className="col-main">
        <div className="phone">
          <div className="phone-bar"><span>Example Bank</span><span>Transfer</span></div>
          {(stage === "form" || stage === "checking") && (
            <form className="phone-body" onSubmit={review}>
              <label className="field"><span>From</span><input value="Current account ··· 4521" readOnly /></label>
              <label className="field"><span>Payee name</span><input value={payee} onChange={(e) => setPayee(e.target.value)} /></label>
              <label className="field"><span>IBAN</span><input value={iban} onChange={(e) => setIban(e.target.value)} /></label>
              <label className="field"><span>Amount (AED)</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
              <button className="btn wide" disabled={stage === "checking" || !payee.trim()}>{stage === "checking" ? "Checking payee" : "Review transfer"}</button>
              <p className="muted small">Try: “Faraz Financial Services”, “4T Global Markets Financial Services”, “Gulf Pearl Securities”.</p>
            </form>)}

          {stage === "stop" && r && (
            <div className="phone-body stop">
              <p className="stop-title">Stop. Check before you send.</p>
              <p>You are about to send <b>AED {amount}</b> to <b>{payee}</b>.</p>
              <TrustCard r={r} compact />
              <button className="btn wide" onClick={() => setStage("cancelled")}>Cancel transfer</button>
              {!sure ? (
                <button className="btn ghost wide" onClick={() => setSure(true)}>I want to continue</button>
              ) : (
                <button className="btn ghost wide danger" onClick={() => setStage("sent")}>I understand the risk. Send AED {amount}</button>)}
            </div>)}

          {stage === "confirm" && (
            <div className="phone-body">
              <p className="stop-title quiet">Confirm transfer</p>
              <dl className="kvs"><div><dt>To</dt><dd>{payee}</dd></div><div><dt>IBAN</dt><dd>{iban}</dd></div><div><dt>Amount</dt><dd>AED {amount}</dd></div></dl>
              <p className="muted small">{r ? `Payee checked against ${r.lists_checked} official lists: ${r.headline.tone === "record" ? r.headline.text : "no warning found (that alone does not prove they are licensed)."}` : "Payee check unavailable."}</p>
              <button className="btn wide" onClick={() => setStage("sent")}>Send AED {amount}</button>
              <button className="btn ghost wide" onClick={reset}>Back</button>
            </div>)}

          {(stage === "sent" || stage === "cancelled") && (
            <div className="phone-body">
              <p className="stop-title quiet">{stage === "sent" ? "Transfer sent (demo)" : "Transfer cancelled"}</p>
              <p>{stage === "sent" ? "In a real bank the money would now be gone." : `AED ${amount} stays in your account.`}</p>
              <button className="btn wide" onClick={reset}>New transfer</button>
            </div>)}
        </div>
      </section>
    </div>);
}
