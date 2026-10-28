/*
 src/lib/speech.ts - voice IN (speech to text) and voice OUT (text to speech), using the browser's own engines
 =============================================================================================================
 Voice IN : Web Speech API (Chrome / Edge). Used by hooks/useHoldToTalk.ts: hold = listen, release = text.
 Voice OUT: speechSynthesis. To sound natural we pick the best installed voice for the language (neural /
            "Natural" / Google voices beat robotic defaults), speak a little slower, sentence by sentence
            (Chrome cuts long utterances after ~15 s), and can be interrupted instantly.
 Better hearing: Chrome's recogniser does not know UAE money words ("120,000 dhs ... retire" was heard as
            "100,000 diplomats ... swim all the time"). So we ask for up to 5 guesses per phrase and keep the one
            that sounds most like a money conversation (pickAlternative). With a Gemini key the server can
            transcribe instead (lib/recorder.ts + /api/transcribe), which is much better at accents and these words.
 Privacy  : Browser recognition sends audio to Google (Chrome) or Microsoft (Edge); server transcription sends it
            to Google Gemini. We can only redact the TEXT afterwards (docs/LIMITATIONS.md).
 (Kept from the previous version; unit tests in speech.test.ts.)
*/
import type { Lang } from "./api";

export const RECOG_LANG: Record<Lang, string> = { en: "en-US", ar: "ar-AE" };

// ---------------------------------------------------------------- speech to text
interface Alternative { transcript: string; confidence?: number }
interface SpeechResult { length: number; [j: number]: Alternative }
interface SpeechResultList { length: number; [i: number]: SpeechResult }
interface RecognitionEvent { results: SpeechResultList }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean; maxAlternatives: number;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionCtor = new () => Recognition;

function ctor(): RecognitionCtor | undefined {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}
export const recognitionSupported = (): boolean => !!ctor();

export interface Recognizer { start(): void; stop(): void }

// Words that make a guess more likely to be what an older adult says to a money-safety guide.
const MONEY_WORDS = /\b(dirhams?|aed|dhs|retire|retirement|retired|pension|gratuity|savings?|saved|salary|income|rent|loan|debt|owe|mortgage|bank|interest|inflation|invest\w*|fund|shares?|gold|licen[sc]ed|scam|company|month|monthly|year|thousand|million|percent|insurance|premium|transfer|my son|my daughter|spend|expenses?)\b/gi;

/** Of several guesses for the same phrase, keep the most money-like one (ties keep the recogniser's first choice). */
export function pickAlternative(alts: string[]): string {
  let best = alts[0] ?? "", bestScore = -1;
  alts.forEach((a, i) => {
    const score = (a.match(MONEY_WORDS)?.length ?? 0) * 2 + (/\d/.test(a) ? 1 : 0) - i * 0.1;
    if (score > bestScore) { best = a; bestScore = score; }
  });
  return best;
}

export function createRecognizer(
  lang: Lang,
  h: { onText: (t: string) => void; onError: (code: string) => void; onEnd: (finalText: string) => void },
): Recognizer {
  const R = ctor();
  if (!R) throw new Error("speech recognition not supported");
  const r = new R();
  r.lang = RECOG_LANG[lang];
  r.continuous = true;          // do not stop at the first pause: the person is holding the button
  r.interimResults = true;      // show words as they are heard
  r.maxAlternatives = 5;        // several guesses per phrase; pickAlternative keeps the most money-like one
  let text = "";
  r.onresult = (e) => {
    let t = "";
    for (let i = 0; i < e.results.length; i++) {
      const res = e.results[i];
      const alts: string[] = [];
      for (let j = 0; j < res.length; j++) alts.push(res[j].transcript);
      t += pickAlternative(alts);
    }
    text = t.trim();
    h.onText(text);
  };
  r.onerror = (e) => h.onError(e.error);
  r.onend = () => h.onEnd(text);
  return { start: () => r.start(), stop: () => r.stop() };
}

// ---------------------------------------------------------------- text to speech
const synth = (): SpeechSynthesis | undefined => ("speechSynthesis" in window ? window.speechSynthesis : undefined);
export const speechOutSupported = (): boolean => !!synth();

/** Higher = nicer. -1 = wrong language. Exported for tests. */
export function voiceScore(v: { name: string; lang: string; localService: boolean; default: boolean }, lang: Lang): number {
  if (!v.lang.toLowerCase().replace("_", "-").startsWith(lang)) return -1;
  const n = v.name.toLowerCase();
  let s = 0;
  if (/natural|neural|online|premium|enhanced|wavenet|studio/.test(n)) s += 5;
  if (/google/.test(n)) s += 3;
  if (!v.localService) s += 1;      // cloud voices usually sound better than the built-in ones
  if (v.default) s += 0.5;
  return s;
}

function voices(): Promise<SpeechSynthesisVoice[]> {
  const s = synth();
  if (!s) return Promise.resolve([]);
  const now = s.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {                 // voices load asynchronously in Chrome
    const done = () => resolve(s.getVoices());
    s.addEventListener("voiceschanged", done, { once: true });
    setTimeout(done, 1500);
  });
}

/** Split into short sentences so no single utterance is long. Exported for tests. */
export function chunks(text: string, max = 180): string[] {
  const sentences = text.split(/(?<=[.!?؟…])\s+/).filter(Boolean);
  const out: string[] = [];
  for (const s of sentences) {
    const last = out[out.length - 1];
    if (last && last.length + s.length + 1 <= max) out[out.length - 1] = `${last} ${s}`;
    else out.push(s);
  }
  return out;
}

let token = 0;                                       // bumping it cancels anything still queued

export function stopSpeaking(): void {
  token++;
  synth()?.cancel();
}

/** Speak `text`. Resolves when finished or interrupted. */
export async function speak(text: string, lang: Lang, onStart?: () => void): Promise<void> {
  const s = synth();
  if (!s || !text.trim()) return;
  stopSpeaking();
  const mine = token;
  const best = (await voices())
    .map((v) => ({ v, score: voiceScore(v, lang) }))
    .filter((x) => x.score >= 0)
    .sort((a, b) => b.score - a.score)[0]?.v;
  if (mine !== token) return;                        // interrupted while voices were loading
  onStart?.();
  for (const part of chunks(text)) {
    if (mine !== token) return;
    await new Promise<void>((resolve) => {
      const u = new SpeechSynthesisUtterance(part);
      u.lang = best?.lang ?? RECOG_LANG[lang];
      if (best) u.voice = best;
      u.rate = 0.95;                                 // a touch slower than default: easier to follow
      u.onend = () => resolve();
      u.onerror = () => resolve();
      s.speak(u);
    });
  }
}
