"""
guardian/guard.py - the FIRST step of every turn: protect before anything else happens
=====================================================================================

WHAT IT DOES
    Runs on the raw words before any agent, any AI and any logging:
        1. redact secrets (guardian/redact.py)
        2. detect the language
        3. spot scam red flags and big irreversible decisions (guardian/signals.py)
        4. decide a PROTECTION LEVEL:
             STOP      a scam may be happening right now (code requested, remote access, "pay a fee to
                       withdraw", a relative in "trouble"...), or a secret was spoken aloud
                       -> a FIXED, human-reviewed script is spoken word for word; no AI, no workflow
             SENT      the person says they ALREADY sent money / shared a code -> urgent harm-reduction script
             PAUSE     a warning sign or a big decision -> the workflow runs, but the answer starts by slowing down
             NORMAL    carry on
    The decision is plain code: safety must not depend on an AI's mood, and it cannot be talked out of its job.

OUTPUT
    GuardDecision(level, clean_text, lang, redacted, signals, high_stakes, referral, recommendation_asked, script)
    Only `clean_text` (redacted) may be stored, logged or sent to any AI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .boundaries import REFER_TO, REFER_TO_AR, asks_for_recommendation, referral_topic
from .lang import detect_language, normalize_arabic
from .redact import SENSITIVE_KINDS, redact
from .signals import Signal, detect, high_stakes

SCRIPTS = {
    "en": {
        "active_scam": (
            "Please pause for a moment. What you describe has the signs of a scam, and I want to protect your money. "
            "Do not share any codes, PINs or passwords, and do not send any money. "
            "If someone is on the phone with you now, hang up. "
            "Then call your bank on the number printed on your card. "
            "If you have already sent money, tell your bank straight away and call the police on 999. "
            "It also helps to tell someone you trust. When you are ready, I can help you check the company's name "
            "on the official Capital Market Authority lists."),
        "shared_secret": (
            "Please stop there, and don't say any more of that number out loud. "
            "I have made sure it is not saved. "
            "Never share a card number, PIN, password or one-time code with anyone, including me. "
            "If you have already given it to someone else, call your bank straight away on the number on your card. "
            "Otherwise, how can I help?"),
        "already_sent": (
            "Thank you for telling me. Acting quickly gives you the best chance. "
            "First, call your bank now on the number printed on your card and ask them to stop or recall the transfer and to "
            "secure your account. Second, report it to the police: call 999 in an emergency, or use the police's "
            "official online reporting service. Third, do not send any more money, even if they promise to return the "
            "first payment for a fee. That is a common second scam. Keep all messages and receipts. "
            "Would you like help working out what to say to your bank?"),
    },
    "ar": {
        "active_scam": (
            "من فضلك توقف لحظة. ما تصفه فيه علامات احتيال، وأريد أن أحمي أموالك. "
            "لا تشارك أي رموز أو أرقام سرية أو كلمات مرور، ولا ترسل أي أموال. "
            "إذا كان أحد على الهاتف معك الآن، أنهِ المكالمة. ثم اتصل بالبنك على الرقم المطبوع على بطاقتك. "
            "وإذا كنت قد أرسلت أموالاً بالفعل، أخبر البنك فوراً واتصل بالشرطة على 999. "
            "ومن المفيد أن تخبر شخصاً تثق به. وعندما تكون مستعداً، أستطيع مساعدتك في التحقق من اسم الشركة في قوائم هيئة رأس المال الرسمية."),
        "shared_secret": (
            "من فضلك توقف هنا، ولا تقل المزيد من هذا الرقم بصوت عالٍ. تأكدت من عدم حفظه. "
            "لا تشارك أبداً رقم البطاقة أو الرقم السري أو كلمة المرور أو رمز التحقق مع أي شخص، بما في ذلك أنا. "
            "وإذا أعطيته لشخص آخر، فاتصل بالبنك فوراً على الرقم المطبوع على بطاقتك. وغير ذلك، كيف يمكنني مساعدتك؟"),
        "already_sent": (
            "شكراً لأنك أخبرتني. التصرف بسرعة يعطيك أفضل فرصة. أولاً، اتصل بالبنك الآن على الرقم المطبوع على بطاقتك "
            "واطلب إيقاف التحويل أو استرجاعه وتأمين حسابك. ثانياً، أبلغ الشرطة: اتصل بـ 999 في حالة الطوارئ أو استخدم خدمة "
            "البلاغات الرسمية. ثالثاً، لا ترسل أي أموال أخرى حتى لو وعدوك بإعادة المبلغ مقابل رسوم، فهذه عملية احتيال ثانية شائعة. "
            "احتفظ بكل الرسائل والإيصالات. هل تريد مساعدة فيما ستقوله للبنك؟"),
    },
}

ALREADY_SENT = re.compile(
    r"\b(i|we|my (wife|husband|mother|father))\b.{0,20}\b(already |just )?(sent|transferred|paid|wired|gave)\b.{0,40}"
    r"\b(money|aed|dirhams?|them|him|her|the (fee|deposit)|\d)"
    r"|\b(already|just) (sent|transferred|paid|gave)\b"
    r"|\bi (gave|shared|told) (him|her|them) (the |my )?(code|otp|pin|password)\b", re.I)
ALREADY_SENT_AR = re.compile(r"(حولت|ارسلت|دفعت|بعثت).{0,30}(فلوس|مبلغ|الف|درهم|له|لهم)|(اعطيته|اعطيتهم).{0,15}(الرمز|الكود|الرقم السري)")


@dataclass
class GuardDecision:
    level: str                                   # STOP | SENT | PAUSE | NORMAL
    clean_text: str
    lang: str = "en"
    redacted: list[str] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    high_stakes: bool = False
    referral: str | None = None
    recommendation_asked: bool = False
    script: str | None = None
    script_id: str | None = None

    @property
    def signal_ids(self) -> list[str]:
        return [s.id for s in self.signals]

    @property
    def referral_to(self) -> str | None:
        return (REFER_TO_AR if self.lang == "ar" else REFER_TO).get(self.referral) if self.referral else None


def guard(raw_text: str, lang: str | None = None) -> GuardDecision:
    clean, kinds = redact(raw_text)
    lang = lang or detect_language(re.sub(r"\[REDACTED:[a-z_]+\]", "", clean))
    norm = normalize_arabic(clean)
    signals = detect(norm, lang)
    score = sum(s.severity for s in signals)
    d = GuardDecision("NORMAL", clean, lang, kinds, signals, high_stakes(norm), referral_topic(norm),
                      asks_for_recommendation(norm))
    scripts = SCRIPTS["ar" if lang == "ar" else "en"]
    if ALREADY_SENT.search(norm) or ALREADY_SENT_AR.search(norm):
        d.level, d.script_id = "SENT", "already_sent"
    elif any(s.severity == 3 for s in signals) or score >= 4:
        d.level, d.script_id = "STOP", "active_scam"
    elif set(kinds) & SENSITIVE_KINDS:
        d.level, d.script_id = "STOP", "shared_secret"
    elif d.high_stakes or score >= 2:
        d.level = "PAUSE"
    if d.script_id:
        d.script = scripts[d.script_id]
    return d
