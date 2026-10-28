/*
 src/hooks/useHoldToTalk.ts - press and hold the microphone to talk
 ===================================================================
 Two ways to turn speech into text, chosen automatically:
   server  (when /api/health says stt = "gemini"): record while held (lib/recorder.ts), send the recording to
           /api/transcribe on release. Much better with UAE accents and money words ("dirhams", "retire").
           While recording the bubble shows "Listening..." instead of live words.
   browser (default, or if recording/transcription fails): the browser's recogniser (lib/speech.ts) shows the
           words live and gives the final text on release.
 Starting to talk interrupts any spoken reply. A very short tap or a silent hold is reported with `onProblem`
 ("unsupported" | "blocked" | "too_short" | "nothing" | "failed"), never sent as a message.
 Works with mouse, touch and keyboard (Space / Enter).
*/
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import { recordingSupported, startRecording, type Recording } from "../lib/recorder";
import { createRecognizer, recognitionSupported, stopSpeaking, type Recognizer } from "../lib/speech";

export type Problem = "unsupported" | "blocked" | "too_short" | "nothing" | "failed";

export function useHoldToTalk(opts: {
  lang: api.Lang; serverStt: boolean; listening: string;
  onSpeech: (text: string) => void; onProblem: (p: Problem) => void;
}) {
  const [holding, setHolding] = useState(false);
  const [live, setLive] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const rec = useRef<Recognizer | null>(null);
  const recording = useRef<Promise<Recording> | null>(null);
  const startedAt = useRef(0);
  const error = useRef<string | null>(null);
  const o = useRef(opts);
  o.current = opts;                                   // always call the newest callbacks

  const beginBrowser = useCallback(() => {
    if (!recognitionSupported()) { setHolding(false); o.current.onProblem("unsupported"); return; }
    const r = createRecognizer(o.current.lang, {
      onText: setLive,
      onError: (code) => { error.current = code; },
      onEnd: (finalText) => {
        rec.current = null;
        setHolding(false);
        setLive("");
        const err = error.current;
        if (err === "not-allowed" || err === "service-not-allowed") return o.current.onProblem("blocked");
        if (finalText) return o.current.onSpeech(finalText);
        if (err && err !== "no-speech" && err !== "aborted") return o.current.onProblem("failed");
        o.current.onProblem(Date.now() - startedAt.current < 500 ? "too_short" : "nothing");
      },
    });
    rec.current = r;
    try { r.start(); } catch { rec.current = null; setHolding(false); o.current.onProblem("failed"); }
  }, []);

  const begin = useCallback(() => {
    if (rec.current || recording.current || transcribing) return;
    stopSpeaking();                                   // the person is talking: stop talking
    error.current = null;
    startedAt.current = Date.now();
    setLive("");
    setHolding(true);
    if (o.current.serverStt && recordingSupported()) {
      setLive(o.current.listening);
      const p = startRecording();
      recording.current = p;
      p.catch((e: unknown) => {                       // no permission / no recorder: use the browser instead
        recording.current = null;
        if (e instanceof DOMException && e.name === "NotAllowedError") { setHolding(false); setLive(""); o.current.onProblem("blocked"); return; }
        beginBrowser();
      });
      return;
    }
    beginBrowser();
  }, [beginBrowser, transcribing]);

  const end = useCallback(async () => {
    if (rec.current) { rec.current.stop(); return; }   // browser path: onEnd delivers the text
    const p = recording.current;
    if (!p) return;
    recording.current = null;
    setHolding(false);
    setLive("");
    let r: Recording;
    try { r = await p; } catch { return; }
    const blob = await r.stop();
    if (Date.now() - startedAt.current < 500) return o.current.onProblem("too_short");
    if (!blob || blob.size < 1500) return o.current.onProblem("nothing");
    setTranscribing(true);
    try {
      const text = await api.transcribe(blob, o.current.lang);
      if (text.trim()) o.current.onSpeech(text.trim());
      else o.current.onProblem("nothing");
    } catch {
      o.current.onProblem("failed");
    } finally {
      setTranscribing(false);
    }
  }, []);

  useEffect(() => () => { rec.current?.stop(); recording.current?.then((r) => r.cancel()).catch(() => {}); }, []);

  /** Spread these onto the button. */
  const bind = {
    onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => { e.currentTarget.setPointerCapture(e.pointerId); begin(); },
    onPointerUp: () => { end(); },
    onPointerCancel: () => { end(); },
    onContextMenu: (e: React.MouseEvent) => e.preventDefault(),   // long-press menu on phones
    onKeyDown: (e: React.KeyboardEvent) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); begin(); } },
    onKeyUp: (e: React.KeyboardEvent) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); end(); } },
  };
  return { holding, live, transcribing, bind };
}
