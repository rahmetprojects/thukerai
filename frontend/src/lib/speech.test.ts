/*
 src/lib/speech.test.ts - unit tests for the voice helpers (picking the most natural voice, splitting speech into sentences).
 Run: cd frontend && npx vitest run
*/
import { describe, expect, it } from "vitest";
import { chunks, pickAlternative, voiceScore } from "./speech";

const v = (name: string, lang: string, localService = true, def = false) => ({ name, lang, localService, default: def });

describe("voiceScore (picks the most natural installed voice)", () => {
  it("rejects other languages", () => expect(voiceScore(v("Zira", "fr-FR"), "en")).toBe(-1));
  it("prefers neural / natural / google voices over built-in ones", () => {
    const basic = voiceScore(v("Microsoft David", "en-US"), "en");
    expect(voiceScore(v("Microsoft Aria Online (Natural)", "en-US", false), "en")).toBeGreaterThan(basic);
    expect(voiceScore(v("Google UK English Female", "en-GB", false), "en")).toBeGreaterThan(basic);
  });
  it("handles Arabic and underscore locales", () => expect(voiceScore(v("Hamed Online (Natural)", "ar_SA", false), "ar")).toBeGreaterThan(0));
});

describe("chunks (avoid Chrome cutting long speech off)", () => {
  it("keeps short text whole", () => expect(chunks("Hello there. How are you?")).toEqual(["Hello there. How are you?"]));
  it("splits long text at sentence ends and never mid-sentence", () => {
    const s = "This is a fairly long sentence number one. ".repeat(6).trim();
    const out = chunks(s, 100);
    expect(out.length).toBeGreaterThan(1);
    out.forEach((c) => { expect(c.length).toBeLessThanOrEqual(100); expect(c.endsWith(".")).toBe(true); });
  });
  it("splits on the Arabic question mark", () => expect(chunks("كيف حالك؟ أنا بخير.", 12)).toHaveLength(2));
});

describe("pickAlternative (prefer money words the recogniser was unsure about)", () => {
  it("keeps the first guess when nothing is better", () => expect(pickAlternative(["hello there", "hello hair"])).toBe("hello there"));
  it("prefers the guess with money words", () =>
    expect(pickAlternative(["I have 120,000 diplomats and I want to swim", "I have 120,000 dirhams and I want to retire"]))
      .toBe("I have 120,000 dirhams and I want to retire"));
});
