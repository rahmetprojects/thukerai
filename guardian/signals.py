"""
guardian/signals.py - scam warning signs in what the person says (plain rules, no AI)
=====================================================================================

WHAT IT DOES
    detect(text) returns every red flag found, each with a plain explanation and what to do instead:
        secret_requested, authority_impersonation, guaranteed_returns, urgency_pressure, secrecy,
        fee_to_withdraw, remote_access, family_emergency, online_platform, licence_by_say_so
    Severity 3 = act now (fixed safety script), 2 = pause and verify, 1 = note.
    high_stakes(text) spots big irreversible actions (selling the home, moving all savings, power of attorney).

SOURCE OF THE PATTERNS
    UAE Ministry of Interior / Central Bank / bank guidance on common frauds. Arabic patterns are a first
    set written without native-speaker review (see docs/LIMITATIONS.md).
    (Ported unchanged from the previous version; regression cases live in tests/test_guardian.py.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    id: str
    severity: int                       # 3 / 2 / 1, see briefing
    patterns: tuple[str, ...]           # English regexes (matched on lower-case text)
    why: str                            # one plain sentence: why this is a warning sign
    do_instead: str                     # what the person should do
    verify_with: tuple[str, ...] = ()   # registry source ids, or "gap:..." for checks we lack
    patterns_ar: tuple[str, ...] = ()   # Arabic regexes (NORMALISED form)
    why_ar: str = ""
    do_instead_ar: str = ""

    def why_for(self, lang: str) -> str:
        return self.why_ar if lang == "ar" and self.why_ar else self.why

    def do_for(self, lang: str) -> str:
        return self.do_instead_ar if lang == "ar" and self.do_instead_ar else self.do_instead


def _p(*pats: str) -> tuple[str, ...]:
    """Tiny helper so the lists below read cleanly."""
    return pats


SIGNALS: list[Signal] = [
    # ---- 1. Someone asks for a code/PIN/password ---------------------------------------
    Signal("secret_requested", 3,
        _p(r"\b(ask|asks|asked|asking|want|wants|wanted|need|needs|needed|tell|told|read|send|share|give)\b.{0,50}\b(otp|pin|cvv|cvc|password|passcode|verification code|security code)\b",
           r"\b(otp|pin|cvv|cvc|password|passcode|verification code|security code)\b.{0,40}\b(ask|asks|asked|asking|want|wants|wanted|need|needs|needed)\b",
           r"\b(ask|asks|asked|asking|want|wants|wanted)\b.{0,30}\bthe code\b",
           r"\bcode\b.{0,30}\b(sent|received|arrived|came)\b.{0,20}\b(phone|sms|text|mobile)\b"),
        "No bank, police officer or government office will ask you for an OTP, PIN, CVV or password.",
        "Stop. Do not share it. Hang up and call your bank on the number printed on your card.",
        patterns_ar=_p(r"(طلب|يطلب|تطلب|طلبت|يبي|يبغي|يبغى|يريد|تريد|عايز|اعطني|اعطيني|ارسل|ابعث).{0,40}(رمز|كود|الرقم السري|كلم[ةه] المرور|كلم[ةه] السر|الباسورد|otp|cvv|pin)",
                       r"(رمز|كود|الرقم السري|كلم[ةه] المرور|الباسورد|otp|cvv|pin).{0,40}(طلب|يطلب|طلبت|يبي|يبغي|يريد|عايز)"),
        why_ar="لا يطلب أي بنك أو شرطة أو جهة حكومية منك رمز التحقق أو الرقم السري أو كلمة المرور.",
        do_instead_ar="توقف. لا تشاركه. أنهِ المكالمة واتصل بالبنك على الرقم المطبوع على بطاقتك."),

    # ---- 2. Someone claims to be the bank/police/ministry ------------------------------
    Signal("authority_impersonation", 3,
        _p(r"\b(call(ed|ing)?|message(d)?|whatsapp(ed)?|text(ed)?|email(ed)?|contact(ed)?)\b.{0,40}\b(from|by) (the |my )?(central bank|police|bank|ministry|emirates id|icp|court|visa|immigration|etisalat|du|customs)\b",
           r"\b(says?|said|claims?|claimed|told me)\b.{0,25}\b(he|she|they)\b.{0,15}\b(is|are|was|were)\b.{0,15}\b(from|with) (the |my )?(central bank|police|bank|ministry|emirates id|icp|court|visa|immigration)\b",
           r"\b(man|woman|person|someone|guy|lady|officer|caller)\b.{0,15}\bfrom (the |my )?(central bank|police|bank|ministry|emirates id|icp|court|immigration)\b",
           r"\b(visa|residency|account|card)\b.{0,30}\b(cancel(l)?ed|blocked|suspended|frozen)\b"),
        "Scammers often pretend to be the Central Bank, police or a ministry to scare you into acting.",
        "Do not act on that call or message. Hang up and contact the organisation using its official number.",
        ("gap:official_contacts",),
        patterns_ar=_p(r"(اتصل|اتصلت|كلمني|كلمتني|يتصل|راسلني|رسال[ةه]|وصلتني|وصلني).{0,40}(من|باسم) ?(ال)?(بنك|مصرف|المركزي|الشرط[ةه]|وزار[ةه]|الهوي[ةه]|المحكم[ةه]|الجمارك)",
                       r"(يقول|قال|يدعي|ادعى).{0,25}(انه|انها|ان).{0,15}من (ال)?(بنك|مصرف|المركزي|الشرط[ةه]|وزار[ةه]|الهوي[ةه])",
                       r"(حسابي|بطاقتي|اقامتي|تاشيرتي).{0,30}(سيتم|تم|راح|بيتم).{0,15}(ايقاف|الغاء|تجميد|تعليق|حظر)"),
        why_ar="ينتحل المحتالون صفة المصرف المركزي أو الشرطة أو الوزارات لإخافتك ودفعك للتصرف.",
        do_instead_ar="لا تتجاوب مع المكالمة أو الرسالة. أنهِ الاتصال وتواصل مع الجهة عبر رقمها الرسمي."),

    # ---- 3. Guaranteed / very high returns ---------------------------------------------
    Signal("guaranteed_returns", 2,
        _p(r"\bguarantee(d|s)?\b.{0,25}\b(profit|profits|return|returns|income|money|gains?)\b",
           r"\brisk[- ]?free\b", r"\bdouble (my|your|the) money\b", r"\bno risk\b",
           r"\b\d{1,3}\s?%\s?(per|a|every|each)\s?(day|week|month)\b",
           r"\b(daily|weekly|monthly) (profit|returns?|income)\b.{0,20}\b\d"),
        "Real investments can lose money. A guaranteed or very high short-term return is a classic fraud sign.",
        "Do not send money. Ask for the company's licence number and check it before anything else.",
        ("cma_licensed_companies", "cma_warnings", "gap:difc_adgm_register"),
        patterns_ar=_p(r"(ربح|ارباح|عائد|عوائد|دخل).{0,20}مضمون", r"مضمون.{0,20}(ربح|ارباح|عائد|عوائد)",
                       r"(بدون|ما في|لا يوجد) مخاطر", r"(ضعف|يضاعف|نضاعف|مضاعف[ةه]).{0,15}(فلوسي|اموالي|مالي|فلوسك|اموالك)",
                       r"\d{1,3}\s?(%|٪|بالمئ[ةه]|بالماي[ةه]|بالمي[ةه])\s?(كل|في|شهريا|يوميا|اسبوعيا)"),
        why_ar="الاستثمارات الحقيقية قد تخسر. الربح المضمون أو العالي جداً في وقت قصير علامة احتيال معروفة.",
        do_instead_ar="لا ترسل أي أموال. اطلب رقم ترخيص الشركة وتحقق منه قبل أي شيء."),

    # ---- 4. Pressure to decide quickly -------------------------------------------------
    Signal("urgency_pressure", 2,
        _p(r"\b(today only|limited (time|spots|places|offer)|act now|act fast|last chance)\b",
           # "before tomorrow" alone is normal ("pay rent before tomorrow"), so it must sit near offer words
           r"\b(offer|deal|invest(ment)?|opportunity|scheme|plan)\b.{0,50}\b(before|by) (midnight|tomorrow|tonight|the end of (the )?day)\b",
           r"\b(before|by) (midnight|tomorrow|tonight)\b.{0,50}\b(offer|deal|invest(ment)?|opportunity|scheme)\b",
           r"\b(must|have to|need to|has to|got to)\s+(decide|answer|reply|respond)\b.{0,20}\b(before|by|today|tonight|tomorrow|now|immediately|quickly|fast)\b",
           r"\b(deadline|expires?)\b.{0,15}\b(today|tonight|tomorrow)\b"),
        "Pressure to decide quickly is a tactic. Honest offers can wait a few days.",
        "Say you need time. Nothing legitimate disappears if you wait and check.",
        patterns_ar=_p(r"(اليوم فقط|فرص[ةه] اخير[ةه]|عرض محدود|لفتر[ةه] محدود[ةه])",
                       r"(لازم|يجب).{0,12}(اقرر|ارد|اجاوب).{0,25}(قبل|اليوم|الان|بسرع[ةه])"),
        why_ar="الضغط لاتخاذ القرار بسرعة أسلوب احتيال. العروض الحقيقية تحتمل الانتظار أياماً.",
        do_instead_ar="قل إنك تحتاج وقتاً. لا شيء مشروع يختفي إذا انتظرت وتحققت."),

    # ---- 5. "Don't tell your family / bank" --------------------------------------------
    Signal("secrecy", 2,
        _p(r"\b(don'?t|do not|dont|must not|should not|shouldn'?t)\b.{0,15}\b(tell|say|mention|inform|talk to)\b.{0,30}\b(anyone|anybody|family|bank|son|daughter|wife|husband|children)\b",
           r"\bkeep (it|this|that) (a )?(secret|confidential|private|between us)\b"),
        "Being told to keep an investment or call secret from family or your bank is a strong warning sign.",
        "Tell someone you trust before doing anything. Genuine firms never ask you to hide things from your family.",
        patterns_ar=_p(r"(لا|ما|مو|ممنوع).{0,6}(تقول|تخبر|تقل|تحكي|تتكلم).{0,25}(احد|لاحد|عائلت|ابنك|بنتك|البنك|اهلك|ولادك|زوجك|زوجتك)",
                       r"(سر|سري).{0,10}بيننا"),
        why_ar="طلب إبقاء الاستثمار أو المكالمة سراً عن عائلتك أو بنكك علامة تحذير قوية.",
        do_instead_ar="أخبر شخصاً تثق به قبل أي خطوة. الجهات الحقيقية لا تطلب منك إخفاء الأمر عن عائلتك."),

    # ---- 6. Fee/tax demanded before you can withdraw -----------------------------------
    Signal("fee_to_withdraw", 3,
        _p(r"\b(pay|paying|send|sending|deposit)\b.{0,30}\b(fee|tax|charge|deposit|commission)\b.{0,50}\b(withdraw|release|unlock|receive|get (my|the) (money|funds|profit))\b",
           r"\b(withdraw|release|unlock)\b.{0,40}\b(need|must|have to|required)\b.{0,20}\b(pay|fee|tax|deposit)\b"),
        "Being asked to pay a fee or tax before you can take your own money out is a common scam.",
        "Do not pay anything more. Stop contact and tell your bank and the police.",
        patterns_ar=_p(r"(رسوم|ضريب[ةه]|عمول[ةه]|ادفع).{0,50}(قبل|حتى|عشان|لكي).{0,25}(اسحب|سحب|استلم|استلام)"),
        why_ar="طلب رسوم أو ضريبة قبل أن تتمكن من سحب أموالك هو أسلوب احتيال شائع.",
        do_instead_ar="لا تدفع شيئاً آخر. اقطع الاتصال وأبلغ البنك والشرطة."),

    # ---- 7. Remote-access apps ---------------------------------------------------------
    Signal("remote_access", 3,
        _p(r"\b(anydesk|teamviewer|quicksupport|remote access|screen shar(e|ing))\b",
           r"\b(install|download)\b.{0,25}\b(app|software|program)\b.{0,30}\b(they|he|she|bank|support)\b"),
        "Giving someone remote access lets them see and move the money in your banking app.",
        "Close the app, turn off the internet, and call your bank from another phone.",
        patterns_ar=_p(r"(anydesk|teamviewer|اني ?ديسك|تيم ?فيور|التحكم عن بعد|مشارك[ةه] الشاش[ةه])"),
        why_ar="منح شخص تحكماً عن بعد يتيح له رؤية أموالك في تطبيق البنك وتحريكها.",
        do_instead_ar="أغلق التطبيق وافصل الإنترنت واتصل بالبنك من هاتف آخر."),

    # ---- 8. "Your grandson is in trouble, send money" ----------------------------------
    Signal("family_emergency", 3,
        _p(r"\b(son|daughter|grandson|granddaughter|nephew|niece|brother|sister|relative|friend)\b.{0,60}\b(arrest(ed)?|accident|hospital|jail|police|stranded|trouble|emergency)\b.{0,80}\b(send|transfer|pay|money)\b",
           r"\b(new|different) (phone )?number\b.{0,40}\b(son|daughter|grandson|granddaughter|nephew|niece)\b"),
        "Fraudsters copy a relative's voice or pose as them on a new number to get urgent money.",
        "Hang up and call your relative on the number you already have, or another family member.",
        patterns_ar=_p(r"(حفيدي|حفيدتي|ابني|بنتي|ابنتي|اخوي|اخي|اختي|قريبي).{0,60}(حادث|مستشفى|سجن|محتجز|القبض|ورط[ةه]|مشكل[ةه]).{0,80}(حول|ارسل|ابعث|فلوس|مبلغ|ادفع|تحويل)"),
        why_ar="يقلد المحتالون صوت قريب أو يتظاهرون بأنهم هو من رقم جديد لطلب مال عاجل.",
        do_instead_ar="أنهِ المكالمة واتصل بقريبك على رقمه المعروف لديك أو بأحد أفراد العائلة."),

    # ---- 9. Crypto / online trading platforms (a note, not an alarm) -------------------
    Signal("online_platform", 1,
        _p(r"\b(crypto(currency)?|bitcoin|usdt|forex|binary options|trading (platform|app|bot|group)|signals? group)\b"),
        "Crypto and online trading platforms are where many fake investment schemes operate.",
        "Check the firm is licensed in the UAE before putting in any money.",
        ("cma_licensed_companies", "cma_warnings", "gap:difc_adgm_register"),
        patterns_ar=_p(r"(بيتكوين|كريبتو|عملات رقمي[ةه]|عملات مشفر[ةه]|فوركس|منص[ةه] تداول|مجموع[ةه] توصيات)"),
        why_ar="العملات الرقمية ومنصات التداول عبر الإنترنت هي المكان الذي تنشط فيه كثير من خطط الاستثمار الوهمية.",
        do_instead_ar="تأكد أن الشركة مرخصة في الإمارات قبل وضع أي مال."),

    # ---- 10. "They say they are licensed" (anyone can say that) ------------------------
    Signal("licence_by_say_so", 1,
        _p(r"\b(says?|said|claims?|claimed|told me)\b.{0,30}\b(licen[sc]ed|regulated|approved|registered)\b",
           r"\bcertificate\b.{0,20}\b(licen[sc]e|regulat)"),
        "Anyone can say they are licensed, and some use forged certificates.",
        "Check the name yourself on the regulator's list, not through a link or number they gave you.",
        ("cma_licensed_companies", "cma_warnings", "gap:difc_adgm_register", "gap:cbuae_register"),
        patterns_ar=_p(r"(يقول|قال|يدعي|ادعى).{0,30}(مرخص|مسجل|معتمد|خاضع)"),
        why_ar="يمكن لأي شخص أن يقول إنه مرخص، وبعضهم يستخدم شهادات مزورة.",
        do_instead_ar="تحقق من الاسم بنفسك في قائمة الجهة المنظمة، وليس عبر رابط أو رقم أعطوك إياه."),
]

# Big, hard-to-undo actions. These trigger PAUSE_AND_VERIFY even with no scam sign.
HIGH_STAKES = re.compile(
    r"\b(power of attorney|sign(ing)? (a|the|this|these)? ?(contract|agreement|papers?|documents?|form)"
    r"|sell(ing)? (my|the|our) (house|villa|apartment|flat|property|land)"
    r"|(withdraw|withdrawing|take out|cash out) (all|everything|my savings)"
    r"|(transfer|send|wire|move)(ring|ing)? (all|everything|my savings|my pension)"
    r"|(transfer|send|wire|move)\b.{0,15}\b\d{4,}\b"
    r"|(give|giving|share|sharing) .{0,15}access to my (account|bank|app))\b", re.I)

HIGH_STAKES_AR = re.compile(
    r"(ابيع|ابغى ابيع|اريد ان ابيع|بيع).{0,12}(بيتي|فيلتي|شقتي|ارضي|عقاري)"
    r"|(توكيل|وكال[ةه] عام[ةه])"
    r"|(حول|تحويل|ارسل|سحب|اسحب).{0,10}(كل|جميع).{0,12}(مدخراتي|فلوسي|اموالي|معاشي)")


def detect(text: str, lang: str = "en") -> list[Signal]:
    """Return every Signal whose patterns match.

    English text is matched as lower-case. For Arabic pass text already run through
    guardian.lang.normalize_arabic. We always run BOTH pattern sets, because people mix languages
    (e.g. an Arabic sentence containing "OTP" or "AnyDesk").
    """
    low = text.lower().replace("’", "'")             # curly apostrophe -> straight
    found = []
    for s in SIGNALS:
        if any(re.search(p, low) for p in s.patterns) or any(re.search(p, text) for p in s.patterns_ar):
            found.append(s)
    return found


def high_stakes(text: str) -> bool:
    return bool(HIGH_STAKES.search(text.replace("’", "'")) or HIGH_STAKES_AR.search(text))
