/*
 src/lib/assistant.ts - talking to the assistant (kitchen/kitchen.py via POST /api/assistant)
 ============================================================================================
 The answer arrives as Server-Sent Events over a POST (EventSource cannot POST), parsed here by hand:
   order   what the front agent understood          recipe  the master's steps
   step    {id, status, detail, ms}  (the ticker)   verify  what the verifier checked
   card    the verified, cited, explained answer    done    {took_ms}
 The types mirror kitchen/composer.py. Nothing here decides anything: the server owns the facts.
*/
import type { Lang, Proposal, Signal, Tone } from "./api";

export type CardType = "fact" | "table" | "number" | "datasets" | "calculation" | "explanation" | "ask" | "proposal"
  | "cannot_answer" | "welcome" | "safety";
export interface Citation {
  n: number; kind: "record" | "dataset" | "search" | "calculation" | "glossary"; label: string;
  dataset_id: string | null; dataset: string | null; record_key: string | null; date: string | null;
  source_page: string | null; fetched_at: string | null; detail: string | null;
}
export interface Cited { text: string; cites: number[] }
export interface FactLine { tone: Tone; text: string; detail: string; dataset: string | null; date: string | null;
  source_page: string | null; cites: number[]; signal: Signal }
export interface Row { dataset_id: string; dataset: string; record_key: string; name: string; date: string | null;
  status: string; category: string; domains: string[]; details: Record<string, unknown>; source_page: string; cites: number[] }
export interface DsLink { dataset_id?: string; id?: string; name: string; rows: number; source_page: string;
  fetched_at: string | null; description?: string; matched?: number | null; cites: number[] }
export interface Offer { term: string; label: string }
export interface AssistantCard {
  id: string; type: CardType; tone: Tone | "neutral"; headline: string; headline_cites: number[];
  sentences: Cited[]; meaning: Cited | null; offers: Offer[]; citations: Citation[]; followups?: string[];
  speech: string; trimmed?: boolean;
  facts?: FactLine[]; not_proven?: string[]; signals?: Signal[];
  rows?: Row[]; number?: number | null; datasets?: DsLink[]; reading?: string[]; planner?: string;
  api?: { url: string; curl?: string; mcp?: { tool: string; arguments: Record<string, unknown> } };
  explain?: { term: string; datasets: DsLink[] };
  ask?: { question: string; quick_replies: string[]; slot: string | null };
  noted?: { label: string; display: string }[];
  calcs?: { tool: string; formula: string; inputs: Record<string, unknown>; outputs: Record<string, unknown> }[];
  assumptions?: string[]; limitations?: string[];
  proposal?: { discovery: string; allowed: boolean; steps: { rung: number; name: string; result: string; detail: string }[];
    proposals: Proposal[] };
}
export interface StepDef { id: string; agent: string; label: string }
export interface StepState { id: string; status: "running" | "done" | "failed"; detail?: string; ms?: number }
export interface OrderInfo { kind: string; decided_by: string; term: string | null; plan: { reading: string[] } | null }

export interface Handlers {
  onOrder?: (o: OrderInfo) => void;
  onRecipe?: (steps: StepDef[]) => void;
  onStep?: (s: StepState) => void;
  onCard: (c: AssistantCard) => void;
  onDone?: (d: { took_ms: number }) => void;
}

/** POST a message and feed the streamed events to the handlers. Resolves when the stream ends. */
export async function ask(session: string, text: string, lang: Lang, mode: "text" | "voice", h: Handlers,
                          signal?: AbortSignal): Promise<void> {
  const res = await fetch("/api/assistant", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ session, text, lang, mode }),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      dispatch(buf.slice(0, i), h);
      buf = buf.slice(i + 2);
    }
  }
  if (buf.trim()) dispatch(buf, h);
}

function dispatch(block: string, h: Handlers) {
  let name = "message", data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return;
  const d = JSON.parse(data);
  if (name === "order") h.onOrder?.(d);
  else if (name === "recipe") h.onRecipe?.(d.steps);
  else if (name === "step") h.onStep?.(d);
  else if (name === "card") h.onCard(d);
  else if (name === "done") h.onDone?.(d);
}

export async function sendImage(session: string, file: File): Promise<AssistantCard> {
  const res = await fetch(`/api/assistant/image?session=${encodeURIComponent(session)}`,
    { method: "POST", body: file, headers: { "Content-Type": file.type || "image/png" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function approve(session: string, discovery: string, proposal: string, overrides?: Record<string, unknown>) {
  const res = await fetch("/api/assistant/approve", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, discovery, proposal, overrides }) });
  return res.json() as Promise<{ ok: boolean; rows?: number; dataset_id?: string; error?: string; reason?: string }>;
}

export const reset = (session: string) =>
  fetch("/api/assistant/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session }) });
