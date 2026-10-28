"""
agents/intents.py - what people ask, mapped to the THREE protection pillars
==========================================================================

WHAT IT DOES
    The catalogue of everything the assistant knows how to help with. Each Intent says:
        pillar      knowledge  - grow retirement & investment know-how, avoid costly mistakes
                    decisions  - reduce the risk of a big money decision (loans, selling, debt, insurance ...)
                    scams      - avoid scams (official warning / licence / finfluencer checks)
        scenarios   which of the 30 benchmark scenarios (docs/SCENARIOS.md) it covers
        patterns    the everyday words that suggest it (weighted regexes, English + Arabic)
        required    the facts it MUST have before calculating - asked ONE AT A TIME, most important first
        optional    facts it uses if offered, never insists on

    SLOTS defines every fact: its type, the plain question we ask for it, how it is read back, and whether
    "none" may mean zero (a pension the person does not have is 0; a savings amount they did not say is UNKNOWN).

    classify(text, extracted_slots) scores every intent and returns the best, or None if nothing is clear
    (the coordinator then asks which of the three pillars the person wants help with - it never guesses).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from guardian.lang import normalize_arabic


@dataclass(frozen=True)
class Slot:
    kind: str                 # money | pct | age | years | int | text | yesno | choice
    ask_en: str
    ask_ar: str
    label: str                # how it is read back: "savings of AED 250,000"
    allow_zero: bool = False  # may "none" mean 0?
    choices: tuple = ()


SLOTS: dict[str, Slot] = {
    "savings": Slot("money", "Roughly how much do you have in savings, in dirhams?", "كم لديك من المدخرات تقريباً بالدرهم؟", "savings"),
    "monthly_spending": Slot("money", "About how much do you spend in a typical month, including rent and bills?",
                             "كم تصرف تقريباً في الشهر، بما في ذلك الإيجار والفواتير؟", "monthly spending"),
    "monthly_income": Slot("money", "Do you receive any regular monthly income, such as a pension, salary or rent? About how much? You can say none.",
                           "هل لديك دخل شهري منتظم مثل معاش أو راتب أو إيجار؟ كم تقريباً؟ يمكنك أن تقول لا يوجد.", "monthly income", True),
    "current_age": Slot("age", "May I ask how old you are? It helps me work out how long the money needs to last.",
                        "هل يمكنني أن أسأل عن عمرك؟ هذا يساعدني في حساب المدة التي يجب أن تكفي فيها الأموال.", "age"),
    "retire_age": Slot("age", "At what age would you like to stop working?", "في أي عمر تريد التوقف عن العمل؟", "planned retirement age"),
    "amount": Slot("money", "How much money are we talking about, in dirhams?", "ما المبلغ الذي نتحدث عنه بالدرهم؟", "an amount"),
    "deposit_rate_pct": Slot("pct", "What interest rate does the account or offer pay, per year?", "ما نسبة الفائدة السنوية للحساب أو العرض؟", "interest rate"),
    "fund_return_pct": Slot("pct", "What return did they say the fund made, in percent?", "ما العائد الذي قالوا إن الصندوق حققه بالنسبة المئوية؟", "fund's past return"),
    "annual_fee_pct": Slot("pct", "Do you know the yearly fee, in percent? You can say you don't know.", "هل تعرف الرسوم السنوية بالنسبة المئوية؟", "yearly fee", True),
    "holding_value": Slot("money", "How much is that holding worth now?", "كم تبلغ قيمة هذا الاستثمار الآن؟", "value of the holding"),
    "total_investable": Slot("money", "And roughly how much are ALL your savings and investments together worth?",
                             "وكم تبلغ قيمة كل مدخراتك واستثماراتك معاً تقريباً؟", "total savings and investments"),
    "loss_pct": Slot("pct", "By roughly what percent has it fallen?", "بكم بالمئة تقريباً انخفضت قيمته؟", "fall"),
    "debt": Slot("money", "How much do you owe in total?", "كم يبلغ مجموع ما عليك من ديون؟", "debt"),
    "debt_rate_pct": Slot("pct", "What interest or profit rate are you charged on it, per year?", "ما نسبة الفائدة أو الربح السنوية عليه؟", "borrowing rate"),
    "savings_rate_pct": Slot("pct", "What interest do your savings earn per year? Say zero if none.", "كم تكسب مدخراتك من فائدة سنوياً؟ قل صفر إن لم يكن هناك شيء.", "savings rate", True),
    "loan_principal": Slot("money", "How much would you borrow?", "كم ستقترض؟", "loan amount"),
    "years": Slot("years", "Over how many years?", "على كم سنة؟", "a period"),
    "quoted_payment": Slot("money", "What monthly payment did they quote? You can say you don't know.", "ما القسط الشهري الذي ذكروه؟", "quoted monthly payment", True),
    "charges": Slot("money", "What are the amounts of the charges you don't recognise?", "ما مبالغ الرسوم التي لا تعرفها؟", "charges"),
    "old_premium": Slot("money", "What did you pay before, per payment?", "كم كنت تدفع سابقاً في كل دفعة؟", "previous premium"),
    "new_premium": Slot("money", "And what is the new premium?", "وكم القسط الجديد؟", "new premium"),
    "sale_price": Slot("money", "Roughly what price could the home sell for?", "بكم يمكن أن يُباع المنزل تقريباً؟", "possible sale price"),
    "mortgage_balance": Slot("money", "Is there a mortgage left on it? How much? You can say none.", "هل بقي عليه رهن عقاري؟ كم؟ يمكنك قول لا يوجد.", "mortgage left", True),
    "annual_rent": Slot("money", "If you sold, what yearly rent would a similar home cost you?", "إذا بعت، كم سيكلفك إيجار منزل مماثل سنوياً؟", "yearly rent"),
    "remit_amount": Slot("money", "How much do you send each month, in dirhams?", "كم ترسل كل شهر بالدرهم؟", "monthly transfer"),
    "exchange_rate": Slot("int", "What exchange rate does your provider give you, for one dirham?", "ما سعر الصرف الذي يعطيه لك مزود الخدمة مقابل درهم واحد؟", "exchange rate"),
    "transfer_fee": Slot("money", "And what fee do they charge per transfer? You can say none.", "وكم رسوم كل تحويل؟", "transfer fee", True),
    "monthly_medical": Slot("money", "About how much do you spend on health each month, including insurance?", "كم تصرف على الصحة شهرياً تقريباً، بما في ذلك التأمين؟", "monthly health costs", True),
    "monthly_support": Slot("money", "About how much do you give your family each month?", "كم تعطي عائلتك شهرياً تقريباً؟", "monthly family support"),
    "invested": Slot("money", "How much did you put in originally?", "كم استثمرت في البداية؟", "amount invested"),
    "stated_rate_pct": Slot("pct", "What yearly return does the statement or the salesperson quote?", "ما العائد السنوي المذكور في الكشف أو من البائع؟", "quoted return"),
    "actual_balance": Slot("money", "What is the balance on your latest statement?", "ما الرصيد في آخر كشف؟", "current balance"),
    "promised_rate_pct": Slot("pct", "What return are they promising, and per month or per year?", "ما العائد الذي يعدونك به، شهرياً أم سنوياً؟", "promised return"),
    "entity_name": Slot("text", "What is the name of the company, person or account? A website address helps too.",
                        "ما اسم الشركة أو الشخص أو الحساب؟ وعنوان الموقع يساعد أيضاً.", "name"),
    "domain": Slot("text", "Do you have their website address?", "هل لديك عنوان موقعهم؟", "website"),
    "cpi_category": Slot("choice", "Which costs: food, housing and rent, transport, medical or education?",
                         "أي تكاليف: الطعام، السكن، النقل، العلاج أم التعليم؟", "price category"),
    "already_sent": Slot("yesno", "Have you already sent them any money or shared any codes?", "هل أرسلت لهم أي أموال أو شاركت أي رموز؟", "already sent money"),
}


@dataclass(frozen=True)
class Intent:
    id: str
    pillar: str
    title: str
    title_ar: str
    scenarios: tuple[int, ...]
    patterns: tuple[tuple[str, float], ...]
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


def P(*pairs) -> tuple:
    return tuple(pairs)


INTENTS: list[Intent] = [
    # ============================================================ PILLAR 1 - knowledge
    Intent("retirement_readiness", "knowledge", "Can I afford to retire?", "هل أستطيع التقاعد؟", (1,),
           P((r"\b(retire|retirement|stop working|stop work|quit work)\b", 2), (r"\bafford\b", 1),
             (r"\b(enough|last)\b", 0.5), (r"تقاعد|اتقاعد|اوقف العمل", 2),
             (r"\b(retired|retiree|pensioner)\b", 1.5), (r"worried about (my )?(money|savings|future|retirement)", 2),
             (r"(savings|money) (will |going to )?(last|be enough)", 1.5),
             (r"how long (will|would|can|could) (it|this|that|my (savings|money)) (last|stretch)", 2.5)),
           ("savings", "monthly_spending", "monthly_income"), ("current_age", "retire_age")),
    Intent("how_much_needed", "knowledge", "How much do I need before I stop working?", "كم أحتاج قبل التوقف عن العمل؟", (5,),
           P((r"how much (should|do|would) i (need|have)", 2.5), (r"before i (stop|retire)", 1.5), (r"emergency fund", 2)),
           ("monthly_spending", "monthly_income", "current_age"), ("retire_age",)),
    Intent("longevity", "knowledge", "Will my money last if I live long?", "هل تكفي أموالي إذا عشت طويلاً؟", (25,),
           P((r"\b(live|living) (to|until|past) (8|9|1)\d", 3), (r"run(ning)? out of money", 2), (r"\b(90|95|100)\b", 0.5),
             (r"outlive", 3), (r"تنفد|تخلص فلوسي", 2)),
           ("savings", "monthly_spending", "monthly_income", "current_age")),
    Intent("purchasing_power", "knowledge", "Is my money keeping up with prices?", "هل تحافظ أموالي على قيمتها؟", (2, 4),
           P((r"\b(inflation|prices? (keep )?(going|go|went|rising|rise|up))\b", 2), (r"worth the same", 2.5),
             (r"(eat|eating|erod)\w* .{0,15}(savings|money)", 2.5), (r"\bin cash\b", 1), (r"purchasing power", 3),
             (r"تضخم|الاسعار|قيمه فلوسي|نفس القيمه", 2),
             (r"\bhow much is inflation\b|\binflation (rate|now|today|this year|in the uae)\b|what is inflation (now|today)", 3),
             (r"\b(food|grocery|rent|housing|petrol|fuel|transport|school|medical|education) (prices?|costs?)\b", 2.5),
             (r"^\s*(and|what about|how about) (food|groceries|rent|housing|petrol|transport|school|medical|education)", 3)),
           (), ("amount", "deposit_rate_pct", "cpi_category")),
    Intent("inflation_news", "knowledge", "Inflation is falling - will my costs go down?", "التضخم ينخفض - هل ستنخفض تكاليفي؟", (17,),
           P((r"inflation (is )?(falling|dropping|down|slowing|lower)", 3), (r"living costs? (will )?go down", 2.5)), ()),
    Intent("income_sufficiency", "knowledge", "Can I live on this income?", "هل يكفيني هذا الدخل؟", (3,),
           P((r"\b(live on|enough to live|cover my (costs|expenses|bills))\b", 3), (r"can i live", 2.5)),
           ("monthly_income", "monthly_spending"), ("monthly_medical",)),
    Intent("fund_vs_bank", "knowledge", "Is this fund better than the bank?", "هل هذا الصندوق أفضل من البنك؟", (10,),
           P((r"\bfund\b.{0,40}\b(return|returned|made|gained)\b", 2.5), (r"better than (leaving|keeping|the bank)", 2),
             (r"last year", 0.5), (r"صندوق.{0,20}(عائد|حقق)", 2.5)),
           ("fund_return_pct", "amount"), ("annual_fee_pct", "deposit_rate_pct", "entity_name")),
    Intent("return_gap", "knowledge", "Why isn't my investment growing as promised?", "لماذا لا ينمو استثماري كما وعدوا؟", (11,),
           P((r"(doesn'?t|does not|isn'?t|is not|not) (seem to )?grow", 3), (r"says? i (earn|get|make) \d", 2),
             (r"(statement|policy) says", 1.5), (r"not growing", 3)),
           ("invested", "years", "stated_rate_pct", "actual_balance"), ("annual_fee_pct",)),
    Intent("concentration", "knowledge", "Too much in one company?", "هل وضعت الكثير في شركة واحدة؟", (9,),
           P((r"\b(one|a single) company'?s? (shares|stock)", 3), (r"\bin [\w ]{1,25}?\b(shares|stocks?)\b", 3),
             (r"should i (buy|get) more\b", 2), (r"\bshares?\b.{0,40}\b(buy more|done well)", 2.5),
             (r"buy more", 1), (r"اسهم شركه واحده", 3), (r"\b(shares?|stocks?)\b", 1.5)),
           ("holding_value", "total_investable")),
    Intent("asset_hype", "knowledge", "Everyone is buying it - should I?", "الجميع يشتري - هل أشتري؟", (8,),
           P((r"\b(gold|silver|bitcoin|crypto|property)\b.{0,50}\b(everyone|everybody|people|talking|hot|boom)", 3),
             (r"(everyone|everybody) is (talking about|buying)", 3), (r"put (my|our) (retirement|savings) (money )?in(to)?", 1.5),
             (r"الذهب|الكل يشتري", 2.5)),
           ("amount", "total_investable")),
    Intent("explain_term", "knowledge", "What does this word mean?", "ماذا تعني هذه الكلمة؟", (),
           P((r"^(what('?s| is| are| does)|explain|meaning of|define)\b.{0,40}\b(mean|means|meaning|inflation|annuity|sukuk|etf|"
              r"fund|bond|dividend|compound|interest|apr|reducing rate|flat rate|diversif|expense ratio|nav)\b", 3),
             (r"ماذا يعني|ما معنى|اشرح", 3)), ()),

    # ============================================================ PILLAR 2 - decisions
    Intent("deposit_offer", "decisions", "Should I take this deposit rate?", "هل أقبل سعر الفائدة هذا؟", (6,),
           P((r"\b(bank|deposit|account)\b.{0,30}\b(offer|offers|offering|pays|gives)\b.{0,20}\d+(\.\d+)?\s*%", 3),
             (r"fixed deposit", 2.5), (r"\binterest\b.{0,20}\b(accept|take)\b", 2), (r"وديعه|فائده.{0,10}\d", 2.5)),
           ("deposit_rate_pct", "amount"), ("years",)),
    Intent("market_drop", "decisions", "The market fell - should I sell?", "السوق هبط - هل أبيع؟", (7, 18),
           P((r"\b(market|stocks?|shares|portfolio)\b.{0,30}\b(fallen|fell|dropped|down|crash|falling|losing)", 3),
             (r"sell (everything|it all|all)", 2.5), (r"(worrying|bad) news .{0,30}(economy|market)", 2.5),
             (r"global economy", 2), (r"السوق.{0,15}(نزل|هبط|طاح)", 3)),
           ("amount", "loss_pct", "monthly_spending"), ("savings",)),
    Intent("debt_vs_savings", "decisions", "Use savings to clear my debt?", "هل أسدد ديني من مدخراتي؟", (12,),
           P((r"\b(owe|debt|loan)\b.{0,60}\bsavings\b", 3), (r"(clear|pay off|pay back|repay|settle) (the |my )?(debt|loan|(credit )?card)", 2.5),
             (r"use (my )?savings", 1.5), (r"اسدد|سداد الدين", 2.5)),
           ("debt", "debt_rate_pct", "savings", "monthly_spending"), ("savings_rate_pct",)),
    Intent("loan_offer", "decisions", "Is this loan affordable?", "هل هذا القرض مناسب؟", (13,),
           P((r"\b(bank|they)\b.{0,20}\boffer(ing|s|ed)?\b.{0,15}\b(me )?(a )?(loan|financ|credit)", 3),
             (r"monthly payment (looks|seems|is) (affordable|ok|fine|small)", 3), (r"personal loan|car loan|take a loan", 2.5),
             (r"\b(take|taking|get|getting|sign for)( out)? (a|an)\b.{0,25}\b(loan|financing|finance)\b", 3),
             (r"\bloan\b.{0,40}\b(flat|over \d+ years|a year|per year)", 2.5),
             (r"\bflat[- ]rate\b.{0,25}\bloan\b", 2.5),
             (r"قرض", 2)),
           ("loan_principal", "debt_rate_pct", "years", "monthly_income", "monthly_spending"), ("quoted_payment",)),
    Intent("unknown_charges", "decisions", "Charges I don't recognise", "رسوم لا أعرفها", (14,),
           P((r"charges? (i )?(don'?t|do not) recogni[sz]e", 3.5), (r"(strange|unknown|unrecogni[sz]ed) (charges?|deductions?)", 3),
             (r"bank statement", 1), (r"رسوم.{0,15}(لا اعرف|غريبه)", 3)), ("charges",)),
    Intent("insurance_premium", "decisions", "My insurance went up - cancel it?", "ارتفع تأميني - هل ألغيه؟", (15,),
           P((r"\b(insurance|premium|policy)\b.{0,40}\b(gone up|went up|increase|higher|cancel)", 3.5),
             (r"cancel (my )?(policy|insurance)", 3), (r"التامين.{0,20}(زاد|ارتفع|الغي)", 3)),
           ("old_premium", "new_premium")),
    Intent("rate_change", "decisions", "Interest rates are changing - does it affect me?", "أسعار الفائدة تتغير - هل يؤثر ذلك علي؟", (16,),
           P((r"interest rates? (are |is )?(chang|ris|going up|going down|cut|fall)", 3.5), (r"rate (cut|hike)", 3),
             (r"اسعار الفائده", 3)),
           ("amount", "deposit_rate_pct")),
    Intent("sell_home", "decisions", "Sell my home and invest the money?", "هل أبيع بيتي وأستثمر المال؟", (19,),
           P((r"sell (my|our|the) (home|house|villa|flat|apartment|property)", 3.5), (r"property (market )?(is )?booming", 2),
             (r"ابيع (بيتي|فيلتي|شقتي)", 3.5)),
           ("sale_price", "mortgage_balance", "annual_rent")),
    Intent("remittance", "decisions", "Sending money abroad - am I getting a fair deal?", "تحويل المال للخارج - هل أحصل على سعر عادل؟", (20,),
           P((r"(send|sends|sending|transfer) (money )?(overseas|abroad|home|back home)", 3.5), (r"exchange rate", 2),
             (r"remittance|exchange house", 3), (r"تحويل.{0,15}(للخارج|لاهلي|بلدي)", 3.5)),
           ("remit_amount", "exchange_rate", "transfer_fee")),
    Intent("medical_costs", "decisions", "Preparing for higher medical costs", "الاستعداد لتكاليف علاج أعلى", (21,),
           P((r"medical (costs?|bills?|expenses?)", 3), (r"health (costs?|insurance)", 2), (r"as i get older", 1),
             (r"تكاليف العلاج|مصاريف طبيه", 3)),
           ("savings", "monthly_spending", "monthly_medical"), ("monthly_income",)),
    Intent("family_support", "decisions", "Supporting family and still retiring", "دعم العائلة والتقاعد", (22,),
           P((r"(children|kids|son|daughter|family) (depend|rely) on me", 3.5), (r"support (my )?(adult )?(children|kids|family)", 3),
             (r"اولادي يعتمدون علي|اصرف على اولادي", 3.5)),
           ("savings", "monthly_spending", "monthly_support", "monthly_income")),
    Intent("job_loss", "decisions", "I lost my job - make savings last", "فقدت عملي - كيف أجعل مدخراتي تكفي", (23,),
           P((r"(lost|lose|losing) my job|laid off|made redundant|unemployed|no longer working", 3.5), (r"فقدت (عملي|وظيفتي)", 3.5)),
           ("savings", "monthly_spending", "monthly_income"), ("current_age",)),
    Intent("relocation", "decisions", "Moving back home when I retire", "العودة إلى بلدي عند التقاعد", (24,),
           P((r"(move|moving|go|going|return) (back )?(home|to my (home )?country)", 3), (r"relocat", 2.5),
             (r"ارجع (لبلدي|الى بلدي)", 3)),
           ("savings", "monthly_spending"), ("monthly_income",)),
    Intent("deposit_protection", "decisions", "Is my money protected if the firm fails?", "هل أموالي محمية إذا أفلست الجهة؟", (26, 28),
           P((r"(bank|company|firm|broker) (fails|goes bankrupt|collapses|goes bust)", 3.5), (r"is my (money|deposit) (safe|protected)", 3),
             (r"deposit (guarantee|protection|insurance)", 3), (r"money is protected", 3), (r"افلس|حمايه الودائع", 3)),
           (), ("entity_name",)),
    Intent("benefit_programme", "decisions", "Is there a government benefit for me?", "هل هناك دعم حكومي لي؟", (29,),
           P((r"(government|new) (benefit|pension|programme|program|scheme|support)", 3.5), (r"can i get (it|this benefit)", 1.5),
             (r"دعم حكومي|برنامج جديد", 3)), ()),

    # ============================================================ PILLAR 3 - scams
    Intent("suspicious_offer", "scams", "Someone is offering me an investment", "عرض استثماري من شخص ما", (30,),
           P((r"(offer|offering|promis)\w*.{0,60}\d+(\.\d+)?\s*%\s*(a|per|every|each) (month|week|day)", 4),
             (r"double (my|your) (money|savings)", 4), (r"(guaranteed|safe|risk[- ]free) (investment|return|profit)", 3),
             (r"\d+(\.\d+)?\s*%\s*(a|per|every|each) (month|week|day)", 3), (r"(friend|someone|whatsapp|instagram).{0,40}(invest|put my savings)", 2.5),
             (r"(ربح|عائد) (مضمون|شهري)", 3.5)),
           ("promised_rate_pct",), ("entity_name", "domain", "savings", "monthly_spending")),
    Intent("check_entity", "scams", "Is this company genuine?", "هل هذه الشركة حقيقية؟", (26, 27),
           P((r"\b(licen[sc]ed|legit|legitimate|genuine|regulated|authori[sz]ed|registered|real company|a scam|scam)\b", 2.5),
             (r"\b(warning|warned)\b.{0,40}\b(company|firm|same)", 3), (r"(on|in) the (regulator|cma|sca)'?s? (website|list)", 3),
             (r"\b(check|verify|look up)\b.{0,30}\b(company|firm|broker|website|platform)", 3),
             (r"^\s*(please )?(check|verify|look up|search for)\b", 2),
             (r"مرخص|احتيال|نصب|حقيقيه|تحذير", 2.5)),
           ("entity_name",), ("domain",)),
    Intent("finfluencer_check", "scams", "Is this influencer allowed to promote investments?", "هل هذا المؤثر مصرح له بالترويج؟", (),
           P((r"\b(influencer|finfluencer|instagram|tiktok|youtube|youtuber|telegram) .{0,40}(invest|stock|trading|crypto|tips|signals)", 3.5),
             (r"(finfluencer|influencer).{0,20}(licen|authori|allowed|legit)", 4), (r"مؤثر|انستقرام|تيك توك", 2.5)),
           ("entity_name",)),
    Intent("unsolicited_contact", "scams", "They contacted me out of the blue", "تواصلوا معي فجأة", (),
           P((r"(contacted|messaged|called|added|texted|approached|reached) me (on|via|through|over|by) "
              r"(whatsapp|telegram|instagram|facebook|tiktok|linkedin|snapchat|phone|sms|text|email|social media)", 4),
             (r"\b(whatsapp|telegram|instagram) (group|message|number|account)", 3),
             (r"cold call|out of the blue|a stranger (called|messaged|contacted)|unknown number", 3),
             (r"(تواصل|اتصل|راسل)\w* معي.{0,15}(واتساب|تيليجرام|انستقرام)", 4)),
           (), ("entity_name",)),
    Intent("scam_tips", "scams", "How do I spot investment scams?", "كيف أكتشف عمليات الاحتيال؟", (),
           P((r"how (do|can) i (spot|recogni[sz]e|avoid|protect myself from) .{0,20}(scam|fraud)", 4),
             (r"(common|signs of) (scams?|fraud)", 3), (r"كيف اعرف الاحتيال|علامات الاحتيال", 4)), ()),
]
BY_ID = {i.id: i for i in INTENTS}
PILLARS = {"knowledge": ("Grow your know-how", "نمِّ معرفتك"),
           "decisions": ("Decide with care", "قرّر بحكمة"),
           "scams": ("Avoid scams", "تجنّب الاحتيال")}


@dataclass
class Classification:
    intent: Intent | None
    score: float
    runner_up: Intent | None = None
    scores: dict = field(default_factory=dict)


def classify(text: str, extracted: dict | None = None) -> Classification:
    t = normalize_arabic(text.lower().replace("’", "'"))
    extracted = extracted or {}
    scores: dict[str, float] = {}
    for it in INTENTS:
        s = sum(w for pat, w in it.patterns if re.search(pat, t))
        if s:
            s += 0.4 * sum(1 for k in it.required if k in extracted)      # facts that fit the intent nudge it up
        scores[it.id] = s
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_id, best = ranked[0]
    if best < 2:
        return Classification(None, best, scores=scores)
    runner = BY_ID[ranked[1][0]] if len(ranked) > 1 and ranked[1][1] >= 2 else None
    return Classification(BY_ID[best_id], best, runner, scores)
