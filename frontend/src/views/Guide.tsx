/*
 src/views/Guide.tsx - the senior voice guide: the same official data, by voice, for someone who won't type
 ==========================================================================================================
 One touchpoint of the trust layer (the others: Check, the bank demo, the browser badge, AI assistants via MCP).
 Empty: a short greeting and example questions. In a conversation: large-type turns; every answer can open
 "How I worked this out" (EvidencePanel). The facts the guide is using appear as chips, so a misheard number is
 easy to spot and correct. Voice: hold the microphone to talk (hooks/useHoldToTalk.ts); replies are read aloud.
 The server (agents/coordinator.py) owns the safety rules and the facts; this file only shows them.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../lib/api";
import { speak, speechOutSupported, stopSpeaking } from "../lib/speech";
import { load, newSessionId, save } from "../lib/storage";
import { STRINGS } from "../i18n";
import { useHoldToTalk, type Problem } from "../hooks/useHoldToTalk";
import { Keyboard, Mic, Send, Speaker } from "../components/Icons";
import { EvidencePanel } from "../components/EvidencePanel";
import { Waveform } from "../components/Waveform";

interface Msg { id: number; role: "user" | "guide"; text: string; r?: api.ChatReply; heard?: { heard: string; as: string }[] }
let nextId = 1;
const EXAMPLES: Record<api.Lang, string[]> = {
  en: ["Someone from Faraz Financial Services promised me 15% a month", "Is this finfluencer authorised? @promoter12",
       "My bank is offering me a loan", "I have AED 250,000 saved. Will it last?"],
  ar: ["شخص يعرض علي ربح 15% شهرياً", "هل هذه الشركة مرخصة؟", "البنك يعرض علي قرضاً", "لدي 250,000 درهم. هل تكفي؟"],
};

export function Guide() {
  const [lang, setLang] = useState<api.Lang>(load("sg_lang") === "ar" ? "ar" : "en");
  const [session, setSession] = useState(() => newSessionId());
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [waiting, setWaiting] = useState(false);
  const [readAloud, setReadAloud] = useState(load("sg_read") !== "off" && speechOutSupported());
  const [speaking, setSpeaking] = useState(false);
  const [draft, setDraft] = useState("");
  const [typeOpen, setTypeOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [openEv, setOpenEv] = useState<number | null>(null);      // which answer's working is shown
  const [facts, setFacts] = useState<api.Noted[]>([]);
  const [serverStt, setServerStt] = useState(false);
  const t = STRINGS[lang];
  const scroller = useRef<HTMLDivElement>(null);
  const readRef = useRef(readAloud); readRef.current = readAloud;
  const langRef = useRef(lang); langRef.current = lang;

  useEffect(() => {
    document.documentElement.lang = lang;
    save("sg_lang", lang);
  }, [lang]);
  useEffect(() => { api.health().then((h) => setServerStt(h.stt === "gemini")).catch(() => {}); }, []);
  useEffect(() => {
    if (msgs.length) scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [msgs, waiting]);
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(""), 4500); return () => clearTimeout(id); }, [notice]);

  const say = useCallback((text: string, l: api.Lang) => {
    if (!readRef.current) return;
    speak(text, l, () => setSpeaking(true)).finally(() => setSpeaking(false));
  }, []);

  const send = useCallback(async (text: string) => {
    const clean = text.trim();
    if (!clean || waiting) return;
    stopSpeaking();
    setNotice("");
    const userId = nextId++;
    setMsgs((m) => [...m, { id: userId, role: "user", text: clean }]);
    setWaiting(true);
    try {
      const r = await api.chat(session, clean, langRef.current);
      const id = nextId++;
      setMsgs((m) => [...m.map((x) => (x.id === userId ? { ...x, heard: r.heard } : x)), { id, role: "guide", text: r.reply, r }]);
      const wide = window.matchMedia?.("(min-width: 1024px)").matches;
      if (r.mode === "answer" && "calculations" in r.evidence && wide) setOpenEv(id);
      setFacts(r.facts || []);
      say(r.reply, r.lang);
    } catch {
      const msg = STRINGS[langRef.current].netError;
      setMsgs((m) => [...m, { id: nextId++, role: "guide", text: msg }]);
      say(msg, langRef.current);
    } finally {
      setWaiting(false);
    }
  }, [session, say, waiting]);

  const onProblem = useCallback((p: Problem) => setNotice(STRINGS[langRef.current].p[p]), []);
  const { holding, live, transcribing, bind } = useHoldToTalk({ lang, serverStt, listening: t.listening, onSpeech: send, onProblem });
  const fixHeard = (text: string) => { setTypeOpen(true); setDraft(text); };

  const startAgain = () => {
    stopSpeaking(); setSpeaking(false); setMsgs([]); setNotice(""); setFacts([]); setOpenEv(null);
    api.reset(session).catch(() => {});
    setSession(newSessionId());
  };
  const toggleRead = () => {
    const on = !readAloud;
    setReadAloud(on); save("sg_read", on ? "on" : "off");
    if (!on) { stopSpeaking(); setSpeaking(false); }
  };
  const submitTyped = (e: React.FormEvent) => { e.preventDefault(); const v = draft; setDraft(""); send(v); };

  const lastGuide = [...msgs].reverse().find((m) => m.role === "guide");
  const lastUserId = [...msgs].reverse().find((m) => m.role === "user")?.id;
  const quick = !waiting && lastGuide?.r?.quick_replies?.length ? lastGuide.r.quick_replies : [];
  const panelMsg = useMemo(() => msgs.find((m) => m.id === openEv && m.r && "calculations" in m.r.evidence), [msgs, openEv]);

  return (
    <div className="guide" dir={lang === "ar" ? "rtl" : "ltr"}>
      <header className="guide-bar">
        {msgs.length > 0 && <button className="btn ghost" onClick={startAgain}>{t.newChat}</button>}
        <span className="spacer" />
        {speechOutSupported() && (
          <button className="btn ghost" onClick={toggleRead} aria-pressed={readAloud}
            aria-label={readAloud ? t.readOn : t.readOff} title={readAloud ? t.readOn : t.readOff}><Speaker off={!readAloud} /></button>)}
        <button className="btn ghost" onClick={() => setLang(lang === "en" ? "ar" : "en")} lang={lang === "en" ? "ar" : "en"}>{t.switchLang}</button>
      </header>

      <div className={`layout${msgs.length === 0 ? " is-home" : ""}`}>
        <main className="convo">
          <div className="chat" ref={scroller} aria-live="polite" aria-label="conversation">
            {msgs.length === 0 ? (
              <section className="home">
                <p className="eyebrow">Voice guide</p>
                <h1 className="display">{t.hello}</h1>
                <p className="lede">{t.intro}</p>
                <div className="examples">
                  {EXAMPLES[lang].map((ex) => (<button key={ex} className="chip" onClick={() => send(ex)}>{ex}</button>))}
                </div>
              </section>
            ) : (
              msgs.map((m) => {
                const r = m.r;
                const tone = r?.level === "STOP" || r?.level === "SENT" ? "urgent" : r?.level === "PAUSE" ? "pause" : "";
                const hasEv = r && "calculations" in r.evidence;
                return (
                  <div key={m.id} className={`turn ${m.role}`}>
                    {r?.pillar && r.intent_title && (<span className="eyebrow topic">{r.intent_title}</span>)}
                    <div className={`bubble ${m.role} ${tone}`} dir="auto">
                      {tone && <span className="tag">{tone === "urgent" ? t.urgent : t.important}</span>}
                      {m.text}
                    </div>
                    {m.role === "user" && m.heard && m.heard.length > 0 && (
                      <span className="heard">{m.heard.map((h) => `${t.heardAs} “${h.heard}” as “${h.as}”`).join(". ")}</span>)}
                    {m.role === "user" && m.id === lastUserId && !waiting && (
                      <button className="linkbtn small-link" onClick={() => fixHeard(m.text)}>{t.fixHeard}</button>)}
                    {hasEv && (
                      <button className="linkbtn small-link" onClick={() => setOpenEv(openEv === m.id ? null : m.id)} aria-expanded={openEv === m.id}>
                        {openEv === m.id ? t.hide : t.how}</button>)}
                    {hasEv && openEv === m.id && (<div className="inline-ev"><EvidencePanel ev={r!.evidence as api.Evidence} t={t} /></div>)}
                  </div>);
              })
            )}
            {holding && live && <div className="turn user"><div className="bubble user live" dir="auto">{live}</div></div>}
            {transcribing && <div className="turn user"><div className="bubble user live">{t.transcribing}...</div></div>}
            {waiting && <div className="bubble typing" role="status" aria-label={t.typing}><i /><i /><i /></div>}
          </div>

          <footer className="dock">
            {notice && <div className="toast" role="status">{notice}</div>}
            {facts.length > 0 && (
              <div className="facts" aria-label={t.usingFacts}>
                <span className="facts-label">{t.usingFacts}:</span>
                {facts.map((f) => (<span key={f.slot} className="fact">{f.label}: <b>{f.display}</b></span>))}
                <button className="fact-clear" onClick={startAgain}>{t.newChat}</button>
              </div>)}
            {quick.length > 0 && (
              <div className="quick">{quick.map((q) => (<button key={q} className="chip solid" onClick={() => send(q)}>{q}</button>))}</div>)}
            <Waveform active={holding || speaking} />
            <div className="mic-row">
              <button className={`mic${holding ? " holding" : ""}`} {...bind} aria-pressed={holding}
                aria-label={holding ? t.release : t.hold}><Mic /></button>
            </div>
            <p className="hint">{holding ? t.release : t.hold}</p>
            <button className="linkbtn" onClick={() => setTypeOpen((o) => !o)} aria-expanded={typeOpen}><Keyboard /> {t.typeInstead}</button>
            {typeOpen && (
              <form className="typed" onSubmit={submitTyped}>
                <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t.placeholder}
                  aria-label={t.typeInstead} maxLength={1500} enterKeyHint="send" autoFocus />
                <button className="round solid" type="submit" aria-label={t.send} disabled={!draft.trim()}><Send /></button>
              </form>)}
          </footer>
        </main>

        <aside className="side" aria-label={t.evidenceTitle}>
          <h2>{t.evidenceTitle}</h2>
          {panelMsg?.r ? (
            <>
              <p className="side-q">{panelMsg.r.intent_title}</p>
              <EvidencePanel ev={panelMsg.r.evidence as api.Evidence} t={t} />
            </>
          ) : <p className="muted">{t.evidenceEmpty}</p>}
        </aside>
      </div>
    </div>
  );
}
