/*
 src/lib/storage.ts - small, safe browser storage + the anonymous conversation id
 ==================================================================================
 load/save wrap localStorage (it can throw in private windows or when blocked; we then keep values in memory).
 sessionId() is a random token that links this browser tab's conversation to the facts the server keeps
 IN MEMORY for this conversation only. "Start again" makes a new one. It is not a name, phone number or account.
*/
const mem: Record<string, string> = {};

export function load(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return mem[key] ?? null; }
}
export function save(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { mem[key] = value; }
}

export function newSessionId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
