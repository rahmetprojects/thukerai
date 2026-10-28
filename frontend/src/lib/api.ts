/*
 src/lib/api.ts - the ONLY place the screen talks to the Python server (api/app.py)
 ===================================================================================
 Typed calls with a timeout, so a slow answer never leaves the person waiting forever.
 The types mirror agents/coordinator.py (TurnResult) and api/app.py (data status).
*/
export type Lang = "en" | "ar";
export type Pillar = "knowledge" | "decisions" | "scams";

export interface Calc {
  tool: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  formula: string;
  assumptions: string[];
  limitations: string[];
}
export interface Candidate {
  match_type: string; score: number; matched_value: string; record_key: string;
  name: string; name_ar?: string; date?: string | null; status?: string; category?: string;
  domains?: string[]; details?: Record<string, unknown>; permitted_activities?: string[];
}
export interface ListResult {
  dataset_id: string; name: string; searched: boolean; match: string;
  snapshot_id: string | null; fetched_at: string | null; candidates: Candidate[];
}
export interface Check {
  type: "entity_check" | "search";
  query?: { name: string; domain: string };
  verdict?: string; flags?: string[]; lists?: ListResult[];
  dataset_id?: string; name?: string; searched?: boolean; match?: string;
  snapshot_id?: string | null; fetched_at?: string | null; candidates?: Candidate[];
}
export interface DataUse {
  dataset_id: string; name: string; publisher: string; source_page: string; status: string;
  snapshot_id: string | null; fetched_at: string | null; rows: number; last_error: string | null;
}
export interface Evidence {
  calculations: Calc[]; checks: Check[]; data: DataUse[]; points: string[]; next_steps: string[];
  assumptions: string[]; limitations: string[]; tools: string[];
}
export interface Noted { slot: string; label: string; value: unknown; display: string }
export interface ChatReply {
  reply: string;
  mode: "answer" | "ask" | "safety" | "clarify";
  lang: Lang;
  pillar: Pillar | null;
  intent: string | null;
  intent_title: string | null;
  question_slot: string | null;
  quick_replies: string[];
  noted: Noted[];
  facts: Noted[];
  evidence: Evidence | Record<string, never>;
  level: "NORMAL" | "PAUSE" | "STOP" | "SENT";
  signals: string[];
  ai_used: boolean;
  heard: { heard: string; as: string }[];              // speech slips the server repaired ("diplomats" -> "dirhams")
}
export interface PillarInfo { id: Pillar; title: string; title_ar: string; examples: string[]; intents: { id: string; title: string }[] }

export interface RunRow {
  run_id: string; dataset_id: string; status: string; rows: number; pages: number; requests: number;
  bytes: number; seconds: number; total_reported: number | null; reason: string; snapshot_id: string | null;
  published: number; at: string; notes: string[] | string; raw_dir: string;
}
export interface DatasetStatus {
  id: string; official_name: string; publisher: string; source_page: string; pillar: Pillar; kind: string;
  current_snapshot: string | null; last_attempt_at: string | null; last_success_at: string | null;
  last_status: string | null; last_error: string | null; row_count: number; as_of: string | null;
  refresh_hours: number; supports: string[]; limitations: string[]; state: string;
  snapshot: { id: string; created_at: string; row_count: number; as_of: string | null; source_url: string;
              total_reported: number | null; pages: number; notes: string[] } | null;
  last_run: RunRow | null;
}
export interface RunSummary {
  run_id: string; started_at: string; finished_at: string; seconds: number;
  counts: Record<string, number>; event_log: string;
  datasets: { dataset_id: string; status: string; rows: number; pages: number; requests: number; bytes: number;
              seconds: number; reason: string; notes: string[]; snapshot_id: string | null; raw_dir: string }[];
}
export interface DataStatus {
  datasets: DatasetStatus[]; latest_run: RunSummary | null; not_fetched: { name: string; reason: string }[];
  refreshing: boolean; live_data: boolean; scheduler_error: string | null;
}
export interface IngestEvent { t: string; run: string; dataset: string; stage: string; level: string; msg: string; [k: string]: unknown }

async function call<T>(path: string, init?: RequestInit, timeoutMs = 45000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, { ...init, signal: ctl.signal, headers: { "Content-Type": "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const chat = (session: string, text: string, lang: Lang) => call<ChatReply>("/api/chat", post({ session, text, lang }));
export const reset = (session: string) => call<{ ok: boolean }>("/api/reset", post({ session }));
export const pillars = () => call<{ pillars: PillarInfo[] }>("/api/pillars");
export async function transcribe(audio: Blob, lang: Lang): Promise<string> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 30000);
  try {
    const res = await fetch(`/api/transcribe?lang=${lang}`, { method: "POST", body: audio, signal: ctl.signal,
      headers: { "Content-Type": audio.type || "audio/webm" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return ((await res.json()) as { text: string }).text;
  } finally {
    clearTimeout(timer);
  }
}
export const health = () => call<{ ok: boolean; ai: string; stt: "gemini" | "browser"; data: { datasets: number; available: number; failing: string[] }; refreshing: boolean }>("/api/health");
export const dataStatus = () => call<DataStatus>("/api/data/status");
export const dataEvents = (dataset?: string, limit = 400) =>
  call<{ run_id: string | null; events: IngestEvent[]; runs: string[] }>(`/api/data/events?limit=${limit}${dataset ? `&dataset=${encodeURIComponent(dataset)}` : ""}`);
export const dataHistory = (id: string) => call<{ runs: RunRow[] }>(`/api/data/history/${encodeURIComponent(id)}`);
export const refreshData = (only?: string[]) => call<{ state: string }>("/api/data/refresh", post({ only }));

// ---------------------------------------------------------------------------- public API v1 (api/v1.py)
export type Tone = "alert" | "caution" | "record" | "none";
export interface Signal { kind: "name" | "domain" | "handle" | "phone"; value: string; shown: string }
export interface Fact {
  tone: Tone; text: string; detail: string; signal: Signal; match: string; matched: string | null;
  dataset_id: string | null; dataset: string | null; record_key: string | null; date: string | null;
  source_page: string | null; fetched_at: string | null; stale: boolean; extra: Record<string, unknown>;
}
export interface TrustReport {
  query: string; signals: Signal[]; headline: { tone: Tone; text: string }; facts: Fact[]; not_proven: string[];
  lists_checked: number; unavailable: string[]; notes: string[]; took_ms: number;
}
export interface Plan { op: string; datasets: string[]; text: string; since: string | null; until: string | null;
  filters: Record<string, string>; limit: number; reading: string[]; planner: string; confident: boolean }
export interface RecordRow { dataset_id: string; dataset: string; record_key: string; name: string; name_ar: string;
  date: string | null; status: string; category: string; domains: string[]; details: Record<string, unknown>;
  source_page: string; fetched_at: string | null; snapshot_id: string }
export interface DsItem { id: string; name: string; why: string; matched: number | null; rows: number; fetched_at: string | null;
  status: string; source_page: string; api: { records: string; csv: string; card: string }; description?: string;
  fields?: { name: string; definition: string }[]; date_means?: string }
export interface AskResult { question: string; plan: Plan; reading: string[]; answer: string; count: number | null;
  datasets: DsItem[]; records: RecordRow[]; report: TrustReport | null; changes?: { dataset_id: string; compared: boolean; added?: number; removed?: number; note?: string }[];
  api: { url: string; curl: string; mcp: { tool: string; arguments: Record<string, unknown> } }; took_ms: number }
export interface Card { id: string; name: string; publisher: string; description: string; description_ar: string;
  keywords: string[]; date_means: string; fields: { name: string; definition: string }[]; source_page: string;
  refresh_hours: number; questions: string[]; limitations: string[];
  state: { status: string; rows: number; fetched_at: string | null; last_verified_at: string | null; last_attempt_status: string | null; last_error: string | null };
  profile: { records?: number; date_from?: string | null; date_to?: string | null; fill?: Record<string, number>; top_categories?: [string, number][] };
  filters: Record<string, string>; api: { records: string; csv: string; card: string } }
export interface Step { rung: number; name: string; result: "found" | "none" | "skipped" | "error"; detail: string }
export interface Proposal { id: string; name: string; publisher: string; rung: number; source_page: string;
  request: Record<string, unknown>; columns: string[]; rows: number; sample: Record<string, string>[];
  normalised_sample: Record<string, unknown>[]; field_map: Record<string, string>; score: number; problems: string[] }
export interface Discovery { url: string; allowed: boolean; steps: Step[]; proposals: Proposal[]; documents: { url: string; title: string }[];
  took_ms: number; requests: number; discovery: string }

export const check = (text: string) => call<TrustReport>("/api/v1/check", post({ text }));
export async function checkImage(file: File): Promise<TrustReport> {
  const res = await fetch("/api/v1/check/image", { method: "POST", body: file, headers: { "Content-Type": file.type || "image/png" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
export const ask = (q: string) => call<AskResult>(`/api/v1/ask?q=${encodeURIComponent(q)}`);
export const runQuery = (url: string) => call<AskResult>(url);
export const catalog = () => call<{ datasets: Card[] }>("/api/v1/datasets");
export const discover = (url: string) => call<Discovery>("/api/v1/onboard/discover", post({ url }), 120000);
export const approve = (discovery: string, proposal: string, overrides?: Record<string, unknown>) =>
  call<{ ok: boolean; dataset_id?: string; status?: string; rows?: number; reason?: string; error?: string }>(
    "/api/v1/onboard/approve", post({ discovery, proposal, overrides }), 120000);
