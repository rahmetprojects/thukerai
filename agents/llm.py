"""
agents/llm.py - OPTIONAL AI help (Gemini or Claude). The app works fully without it.
===================================================================================

WHAT IT DOES
    Narrow jobs, all checked by plain code afterwards (extract_facts: every number it returns must appear in the
    person's own words, so it can map "I spend about ten thousand" to monthly spending but never invent a figure):
      1. understand(text, intents)  when the rule-based classifier cannot tell what the person wants,
                                    ask the model to pick ONE intent id from the catalogue (JSON only).
                                    The answer is accepted only if it is a known intent id.
      2. rephrase(draft, facts, lang)  turn the deterministic template answer into warmer, shorter speech, or
                                    into Arabic. The validator (agents/validator.py) then checks every number
                                    against the evidence; if anything is off, the template is used instead.

      3. transcribe(audio, lang)    OPTIONAL better speech-to-text (Gemini only): the recording is transcribed
                                    with a prompt that expects UAE money words (dirhams, AED, retire, pension,
                                    gratuity...). Browser recognition misheard "120,000 dhs ... retire" as
                                    "100,000 diplomats ... swim all the time". Off unless GEMINI_API_KEY is set.

      4. read_image(image)          OPTIONAL: transcribe the text in a screenshot (a WhatsApp message, an ad) for
                                    the Trust Report. Code then decides what to look up (trust/signals.py).
      5. plan_query(question, cat)  OPTIONAL: turn a messy data question into a query PLAN (JSON) for the
                                    explorer. explore/planner.py validates every field against the catalogue
                                    and falls back to the rule planner if anything is unknown.

    The model NEVER does arithmetic, never decides whether a name matches, never writes a result.

CONFIGURATION
    GEMINI_API_KEY (free tier at aistudio.google.com) -> Gemini (pip install google-genai)
    ANTHROPIC_API_KEY                                   -> Claude  (pip install anthropic)
    Neither set -> offline mode: rule-based understanding + template answers (fully tested).

PRIVACY
    Only REDACTED text and computed facts are sent. Check your provider's data terms before using real
    people's details (Gemini's free tier may use prompts to improve Google's products).
"""
from __future__ import annotations

import json
import logging
import os
import re

import config

log = logging.getLogger("agents.llm")

REPHRASE_SYSTEM = """You rewrite an answer for an older adult in the UAE who is LISTENING to it (voice).
Rules you must follow exactly:
- Keep EVERY number, amount, percentage and date exactly as written in the draft, written with digits (0-9). Add no new numbers.
- Do not add facts, advice, products or recommendations that are not in the draft. Never say anything is safe, guaranteed or legitimate.
- Keep every caution and limitation in the draft.
- Short sentences, plain words, warm and respectful. No lists, markdown, brackets or emojis. At most 140 words.
- Language: {lang}."""

TRANSCRIBE_PROMPT = """Transcribe exactly what the speaker says in this recording, in {lang}.
Context: an older adult in the United Arab Emirates is talking to a money-safety assistant about savings, retirement,
pensions, loans, banks, investments or possible scams. Expect words such as: dirhams, AED, Dhs, retire, retirement,
pension, gratuity, end of service, savings, salary, rent, loan, debt, mortgage, interest, inflation, gold, shares,
fund, broker, licensed, Capital Market Authority, CMA, Central Bank, scam, WhatsApp, my son, my daughter.
Write amounts with digits (120,000), keep percentages as digits with %, and do not correct what was said
into something the speaker did not say. Output ONLY the transcript, nothing else. If there is no speech, output nothing."""

UNDERSTAND_SYSTEM = """Classify what an older adult in the UAE wants help with. Reply with JSON only:
{{"intent": "<one id from the list, or none>"}}
Intents:
{catalogue}"""


class LLM:
    def __init__(self):
        self.provider, self.model, self.client = "offline", None, None
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            try:
                from google import genai
                self.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
                self.provider, self.model = "gemini", config.GEMINI_MODEL
            except Exception as e:                       # SDK missing or broken: stay offline, say why
                log.warning("Gemini key set but SDK unavailable (%s); running offline", e)
        if self.client is None and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic
                self.client = anthropic.Anthropic(timeout=config.LLM_TIMEOUT)
                self.provider, self.model = "claude", config.CLAUDE_MODEL
            except Exception as e:
                log.warning("Anthropic key set but SDK unavailable (%s); running offline", e)
        log.info("AI helper: %s%s", self.provider, f" ({self.model})" if self.model else "")

    @property
    def available(self) -> bool:
        return self.client is not None

    def _complete(self, system: str, user: str, json_mode: bool = False) -> str | None:
        if not self.client:
            return None
        try:
            if self.provider == "gemini":
                from google.genai import types
                cfg = types.GenerateContentConfig(system_instruction=system, max_output_tokens=config.MAX_REPLY_TOKENS * 3,
                                                  response_mime_type="application/json" if json_mode else None)
                r = self.client.models.generate_content(model=self.model, contents=user, config=cfg)
                return (r.text or "").strip() or None
            r = self.client.messages.create(model=self.model, max_tokens=config.MAX_REPLY_TOKENS, system=system,
                                            messages=[{"role": "user", "content": user}])
            return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip() or None
        except Exception as e:                           # network, quota, auth: the caller falls back to templates
            log.warning("AI call failed (%s: %s); using the template answer", type(e).__name__, str(e)[:200])
            return None

    @property
    def can_transcribe(self) -> bool:
        return self.provider == "gemini" and self.client is not None

    def transcribe(self, audio: bytes, mime: str, lang: str = "en") -> str | None:
        """Speech to text with UAE money vocabulary. None on any failure (the screen then uses the browser's text)."""
        if not self.can_transcribe or not audio:
            return None
        try:
            from google.genai import types
            prompt = TRANSCRIBE_PROMPT.format(lang="Arabic or English, as spoken" if lang == "ar" else "English")
            r = self.client.models.generate_content(
                model=self.model, contents=[types.Part.from_bytes(data=audio, mime_type=mime.split(";")[0]), prompt],
                config=types.GenerateContentConfig(max_output_tokens=400, temperature=0))
            text = (r.text or "").strip().strip('"')
            log.info("transcribed %d bytes of audio -> %d characters", len(audio), len(text))
            return text or None
        except Exception as e:
            log.warning("transcription failed (%s: %s); the browser's text will be used", type(e).__name__, str(e)[:200])
            return None

    @property
    def can_read_images(self) -> bool:
        return self.client is not None

    def read_image(self, image: bytes, mime: str = "image/png") -> str | None:
        """All text visible in a screenshot, verbatim. None on failure (the person is asked to paste the text)."""
        if not self.client or not image:
            return None
        prompt = ("Transcribe ALL text visible in this screenshot exactly as written, including names, website "
                  "addresses, @handles and phone numbers. Output only the text, no commentary.")
        try:
            if self.provider == "gemini":
                from google.genai import types
                r = self.client.models.generate_content(
                    model=self.model, contents=[types.Part.from_bytes(data=image, mime_type=mime), prompt],
                    config=types.GenerateContentConfig(max_output_tokens=1200, temperature=0))
                text = (r.text or "").strip()
            else:
                import base64
                r = self.client.messages.create(model=self.model, max_tokens=1200, messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime,
                                                 "data": base64.b64encode(image).decode()}},
                    {"type": "text", "text": prompt}]}])
                text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
            log.info("read %d image bytes -> %d characters", len(image), len(text))
            return text or None
        except Exception as e:
            log.warning("image reading failed (%s: %s)", type(e).__name__, str(e)[:200])
            return None

    def plan_query(self, question: str, catalogue: str, today: str) -> dict | None:
        """A JSON query plan for the explorer, or None. Validated by explore/planner.py before use."""
        system = ("You turn a question about official UAE financial datasets into a query plan. Reply with JSON only:\n"
                  '{"op": "list|count|latest|lookup|find_datasets|changes", "datasets": ["<ids>"], '
                  '"text": "<words to search for, or empty>", "since": "YYYY-MM-DD or null", '
                  '"until": "YYYY-MM-DD or null", "filters": {"<field>": "<value>"}, "limit": 20}\n'
                  f"Today is {today}. Use ONLY dataset ids and filter fields from this catalogue:\n{catalogue}")
        out = self._complete(system, question, json_mode=True)
        if not out:
            return None
        try:
            m = re.search(r"\{.*\}", out, re.S)
            data = json.loads(m.group(0) if m else out)
            return data if isinstance(data, dict) else None
        except (ValueError, AttributeError):
            return None

    def extract_facts(self, text: str, wanted: dict[str, str]) -> dict | None:
        """Read the facts we still need from garbled speech. Returns {slot: number}. The coordinator ACCEPTS a value
        only if that exact number appears in the person's words, so the AI can map, never invent."""
        if not self.client:
            return None
        system = ("Extract numbers an older adult in the UAE said about their money. Reply with JSON only, using ONLY "
                  "these keys, and ONLY for numbers literally present in the text (amounts in AED as plain numbers, "
                  "percentages as numbers): " + ", ".join(f'"{k}" ({v})' for k, v in wanted.items())
                  + ". Omit a key if the text does not clearly give it. Never estimate.")
        out = self._complete(system, text, json_mode=True)
        if not out:
            return None
        try:
            m = re.search(r"\{.*\}", out, re.S)
            data = json.loads(m.group(0) if m else out)
        except (ValueError, AttributeError):
            return None
        return {k: v for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

    def understand(self, text: str, catalogue: list[tuple[str, str]]) -> str | None:
        cat = "\n".join(f"- {i}: {t}" for i, t in catalogue)
        out = self._complete(UNDERSTAND_SYSTEM.format(catalogue=cat), text, json_mode=True)
        if not out:
            return None
        try:
            m = re.search(r"\{.*\}", out, re.S)
            intent = json.loads(m.group(0) if m else out).get("intent")
        except (ValueError, AttributeError):
            return None
        return intent if intent in {i for i, _ in catalogue} else None

    def rephrase(self, draft: str, lang: str) -> str | None:
        language = "Arabic (clear Modern Standard Arabic that is easy to understand when read aloud)" if lang == "ar" else "English"
        return self._complete(REPHRASE_SYSTEM.format(lang=language), draft)
