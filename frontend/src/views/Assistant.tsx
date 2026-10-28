/*
 src/views/Assistant.tsx - the one screen: ask anything, by voice or text; get a cited, explained answer
 ======================================================================================================
 The person sees ONE assistant. Behind it (kitchen/): a front agent takes the order, a master agent writes a
 recipe and runs the specialists, the explainer says what it means, the verifier checks every number and source.
 This screen only shows that work:
   * the ticker under each question lists the recipe's steps live, as the server finishes them (honest progress)
   * each answer is an AnswerCard: headline, facts, WHAT THIS MEANS, "Explain" chips, follow-ups, numbered sources
 VOICE
   * hold the microphone (or Space) to talk; release to send. Server transcription (Gemini) when configured,
     otherwise the browser's recogniser (hooks/useHoldToTalk.ts). Talking interrupts the voice (barge-in).
   * a question asked by voice is answered by voice: the card's `speech` (at most three short sentences, ending
     with "Would you like me to explain ...?"), so "yes" works as the next spoken turn.
   * if the answer takes longer than 1.5 s, a short spoken cue says what is happening, so silence never feels broken.
 A screenshot of an offer can be dropped in (paperclip) and is checked like a message.
*/
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import * as A from "../lib/assistant";
import { speak, speechOutSupported, stopSpeaking } from "../lib/speech";
import { load, newSessionId, save } from "../lib/storage";
import { useHoldToTalk, type Problem } from "../hooks/useHoldToTalk";
import { Mic, Send, Speaker } from "../components/Icons";
import { AnswerCard } from "../components/AnswerCard";

interface Turn {
  id: number; text: string; mode: "text" | "voice"; steps: A.StepDef[]; state: Record<string, A.StepState>;
  card?: A.AssistantCard; error?: string; took?: number; kind?: string;
}
let nextId = 1;
const EXAMPLES = [
  ["Check a website", "Is market10.net licensed?"],
  ["Check a message", "BitWealth Pro Capital promised me 15% a month, is it real?"],
  ["Ask the data", "Which firms were warned against this month?"],
  ["Explain a term", "What is a passported fund?"],
  ["Plan money", "I have 300,000 saved. How long will it last?"],
] as const;
const PROBLEM: Record<Problem, string> = {
  unsupported: "This browser cannot listen. Type instead, or use Chrome or Edge.",
  blocked: "The microphone is blocked. Allow it in the address bar, then try again.",
  too_short: "Hold the button while you talk, then let go.", nothing: "I didn't hear anything. Try again a little closer.",
  failed: "I couldn't hear that clearly. Please try again.",
};

export function Assistant() {
  const [session, setSession] = useState(() => newSessionId());
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [voiceOut, setVoiceOut] = useState(load("sg_voice") !== "off" && speechOutSupported());
  const [speaking, setSpeaking] = useState(false);
  const [serverStt, setServerStt] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const voiceRef = useRef(voiceOut); voiceRef.current = voiceOut;

  useEffect(() => { api.health().then((h) => setServerStt(h.stt === "gemini")).catch(() => {}); }, []);
  useEffect(() => { save("sg_voice", voiceOut ? "on" : "off"); if (!voiceOut) stopSpeaking(); }, [voiceOut]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(t); }, [notice]);

  const say = useCallback((text: string) => {
    if (!voiceRef.current || !text) return;
    speak(text, "en", () => setSpeaking(true)).finally(() => setSpeaking(false));
  }, []);

  const patch = (id: number, f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));

  const send = useCallback(async (raw: string, mode: "text" | "voice" = "text") => {
    const text = raw.trim();
    if (!text || busy) return;
    stopSpeaking();
    setDraft("");
    const id = nextId++;
    setTurns((ts) => [...ts, { id, text, mode, steps: [], state: {} }]);
    setBusy(true);
    let answered = false;
    const cue = mode === "voice" ? setTimeout(() => { if (!answered) say("One moment, I'm checking the official data."); }, 1500) : 0;
    try {
      await A.ask(session, text, "en", mode, {
        onOrder: (o) => patch(id, (t) => ({ ...t, kind: o.kind })),
        onRecipe: (steps) => patch(id, (t) => ({ ...t, steps })),
        onStep: (s) => patch(id, (t) => ({ ...t, state: { ...t.state, [s.id]: s } })),
        onCard: (card) => {
          answered = true;
          patch(id, (t) => ({ ...t, card }));
          if (mode === "voice") say(card.speech);
        },
        onDone: (d) => patch(id, (t) => ({ ...t, took: d.took_ms })),
      });
    } catch {
      patch(id, (t) => ({ ...t, error: "I could not reach the assistant. Please try again." }));
    } finally {
      clearTimeout(cue);
      setBusy(false);
    }
  }, [busy, session, say]);

  const talk = useHoldToTalk({
    lang: "en", serverStt, listening: "Listening…",
    onSpeech: (t) => send(t, "voice"), onProblem: (p) => setNotice(PROBLEM[p]),
  });

  const image = async (f: File | undefined) => {
    if (!f || busy) return;
    const id = nextId++;
    setTurns((ts) => [...ts, { id, text: `Screenshot: ${f.name}`, mode: "text", steps: [{ id: "img", agent: "reader", label: "Read the screenshot" }], state: { img: { id: "img", status: "running" } } }]);
    setBusy(true);
    try {
      const card = await A.sendImage(session, f);
      patch(id, (t) => ({ ...t, card, state: { img: { id: "img", status: "done" } } }));
    } catch {
      patch(id, (t) => ({ ...t, error: "Send a PNG, JPEG or WebP image under 8 MB." }));
    } finally { setBusy(false); }
  };

  const approve = async (discovery: string, proposal: string, name: string) => {
    const r = await A.approve(session, discovery, proposal, { official_name: name });
    return r.ok ? `Loaded ${r.rows} rows. You can ask about “${name}” now.` : `Not loaded: ${r.error ?? r.reason ?? "unknown problem"}`;
  };

  const restart = () => { stopSpeaking(); A.reset(session); setSession(newSessionId()); setTurns([]); };

  return (
    <div className="assistant">
      <div className="thread">
        {!turns.length && (
          <section className="intro">
            <h1 className="display">Ask about any company, website, fund or money decision.</h1>
            <p className="lede">Answers come only from official UAE data. Every fact is numbered and linked to the record it came
              from, and any financial term is explained if you want.</p>
            <ul className="examples-list">
              {EXAMPLES.map(([label, q]) => (
                <li key={q}><button className="example" onClick={() => send(q)}><span className="eyebrow">{label}</span><span className="ex-q">{q}</span></button></li>))}
            </ul>
          </section>)}

        {turns.map((t) => (
          <section key={t.id} className="turn">
            <p className="you"><span className="eyebrow">{t.mode === "voice" ? "You said" : "You asked"}</span>{t.text}</p>
            {!!t.steps.length && <Ticker steps={t.steps} state={t.state} took={t.took} done={!!t.card} />}
            {!t.card && !t.error && !t.steps.length && <p className="muted small working">Reading your question…</p>}
            {t.error && <p className="banner bad">{t.error}</p>}
            {t.card && <AnswerCard c={t.card} busy={busy} onAsk={(q) => send(q, t.mode)} onApprove={approve} />}
          </section>))}
        <div ref={end} />
      </div>

      <footer className="composer">
        {notice && <p className="notice" role="status">{notice}</p>}
        {talk.holding && <p className="live" aria-live="polite">{talk.live || "Listening…"}</p>}
        {talk.transcribing && <p className="live">Writing down what you said…</p>}
        <form className="bar" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
          <button type="button" className={`mic${talk.holding ? " on" : ""}`} aria-label="Hold to talk" disabled={busy && !talk.holding} {...talk.bind}><Mic /></button>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type a name, website, message or question" aria-label="Your question" maxLength={2000} />
          <label className="clip" title="Check a screenshot"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { image(e.target.files?.[0]); e.target.value = ""; }} />＋</label>
          <button className="btn" type="submit" disabled={busy || !draft.trim()} aria-label="Send"><Send /></button>
        </form>
        <div className="row small muted">
          <span>Hold the microphone or press Space on it to talk.</span>
          <span className="spacer" />
          {speechOutSupported() && (
            <button className="linkish" onClick={() => { if (speaking) stopSpeaking(); else setVoiceOut(!voiceOut); }}>
              <Speaker off={!voiceOut} /> {speaking ? "Stop speaking" : voiceOut ? "Voice replies on" : "Voice replies off"}</button>)}
          {!!turns.length && <button className="linkish" onClick={restart}>Start again</button>}
        </div>
      </footer>
    </div>);
}

/** The recipe, live: one line per step; the agent that does it; what it found; how long it took. */
function Ticker({ steps, state, took, done }: { steps: A.StepDef[]; state: Record<string, A.StepState>; took?: number; done: boolean }) {
  const [open, setOpen] = useState(false);
  const all = steps.every((s) => state[s.id]?.status === "done");
  if (done && all && !open) {
    return (<button className="ticker-sum" onClick={() => setOpen(true)}>
      {steps.length} step{steps.length > 1 ? "s" : ""} · {steps.map((s) => s.agent).filter((a, i, x) => x.indexOf(a) === i).join(", ")}
      {took ? ` · ${took < 1000 ? `${Math.round(took)} ms` : `${(took / 1000).toFixed(1)} s`}` : ""} · show work</button>);
  }
  return (
    <ol className="ticker" onClick={() => done && setOpen(false)}>
      {steps.map((s) => {
        const st = state[s.id]?.status ?? "waiting";
        return (<li key={s.id} className={`st-${st}`}>
          <span className="tk-mark" aria-hidden="true" />
          <span className="tk-agent">{s.agent}</span>
          <span className="tk-label">{s.label}</span>
          <span className="tk-detail">{state[s.id]?.detail ?? ""}{state[s.id]?.ms != null ? ` · ${state[s.id]!.ms} ms` : ""}</span>
        </li>);
      })}
    </ol>);
}
