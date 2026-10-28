/*
 src/i18n.ts - every word the screen shows, in English and Arabic
 =================================================================
 Arabic is a first draft and needs a native speaker's review (docs/LIMITATIONS.md).
 To add a language: add a key here and to the Lang type in lib/api.ts.
*/
import type { Lang, Pillar } from "./lib/api";

export interface Strings {
  hello: string; tagA: string; tagB: string; intro: string;
  pillars: Record<Pillar, { title: string; line: string }>;
  hold: string; release: string; typeInstead: string; placeholder: string; send: string;
  newChat: string; readOn: string; readOff: string; switchLang: string; data: string; back: string;
  how: string; hide: string; calculations: string; records: string; sources: string; assumptions: string;
  limits: string; nextSteps: string; usingFacts: string; important: string; urgent: string; typing: string;
  noEvidence: string; listening: string; transcribing: string; fixHeard: string; heardAs: string; evidenceTitle: string; evidenceEmpty: string; asked: string;
  p: { unsupported: string; blocked: string; too_short: string; nothing: string; failed: string };
  netError: string;
}

export const STRINGS: Record<Lang, Strings> = {
  en: {
    hello: "Hello.", tagA: "Let's protect your ", tagB: "hard-earned money.",
    intro: "Ask me anything about your savings, a money decision, or an offer you are unsure about. Hold the button and speak, or tap a question.",
    pillars: {
      knowledge: { title: "Grow your know-how", line: "Will my savings last? Is my money keeping up with prices?" },
      decisions: { title: "Decide with care", line: "Loans, debts, selling a home, insurance, sending money abroad." },
      scams: { title: "Avoid scams", line: "Check a company, a website or an influencer against official lists." },
    },
    hold: "Hold to talk", release: "Release to send", typeInstead: "Type instead", placeholder: "Type your question",
    send: "Send", newChat: "Start again", readOn: "Reading aloud is on", readOff: "Reading aloud is off",
    switchLang: "العربية", data: "Data", back: "Back to the conversation",
    how: "How I worked this out", hide: "Hide the working", calculations: "Calculations", records: "Official records checked",
    sources: "Data used", assumptions: "Assumptions", limits: "What this cannot tell you", nextSteps: "Next steps",
    usingFacts: "Facts I am using", important: "Please pause", urgent: "Act now", typing: "The guide is working it out",
    noEvidence: "No calculation was needed for this answer.",
    listening: "Listening...", transcribing: "Writing down what you said", fixHeard: "Fix what I heard", heardAs: "I read",
    evidenceTitle: "How I worked this out", evidenceEmpty: "When I calculate something or check an official list, the working appears here.",
    asked: "I asked",
    p: {
      unsupported: "Voice needs Chrome or Edge. You can type instead.",
      blocked: "The microphone is blocked. Allow it in the address bar, or type instead.",
      too_short: "Keep holding the button while you speak.",
      nothing: "I did not hear anything. Hold the button while you speak.",
      failed: "The microphone did not work. Please try again, or type instead.",
    },
    netError: "I could not reach the guide just now. Please try again.",
  },
  ar: {
    hello: "مرحباً.", tagA: "لنحمِ ", tagB: "أموالك التي تعبت في جمعها.",
    intro: "اسألني عن مدخراتك أو قرار مالي أو عرض لست متأكداً منه. اضغط على الزر وتحدث، أو اختر سؤالاً.",
    pillars: {
      knowledge: { title: "نمِّ معرفتك", line: "هل تكفي مدخراتي؟ هل تحافظ أموالي على قيمتها؟" },
      decisions: { title: "قرّر بحكمة", line: "القروض والديون وبيع المنزل والتأمين والتحويل للخارج." },
      scams: { title: "تجنّب الاحتيال", line: "تحقق من شركة أو موقع أو مؤثر في القوائم الرسمية." },
    },
    hold: "اضغط مطولاً للتحدث", release: "ارفع إصبعك للإرسال", typeInstead: "اكتب بدلاً من ذلك", placeholder: "اكتب سؤالك",
    send: "إرسال", newChat: "ابدأ من جديد", readOn: "القراءة بصوت عالٍ مفعّلة", readOff: "القراءة بصوت عالٍ متوقفة",
    switchLang: "English", data: "البيانات", back: "العودة إلى المحادثة",
    how: "كيف توصلت إلى ذلك", hide: "إخفاء التفاصيل", calculations: "الحسابات", records: "السجلات الرسمية التي تم فحصها",
    sources: "البيانات المستخدمة", assumptions: "الافتراضات", limits: "ما لا يمكن أن يخبرك به هذا", nextSteps: "الخطوات التالية",
    usingFacts: "المعلومات التي أستخدمها", important: "تمهّل من فضلك", urgent: "تصرّف الآن", typing: "المرشد يعمل على الإجابة",
    noEvidence: "لم تكن هناك حاجة لأي حساب في هذه الإجابة.",
    listening: "أستمع...", transcribing: "أكتب ما قلته", fixHeard: "صحّح ما سمعته", heardAs: "فهمت",
    evidenceTitle: "كيف توصلت إلى ذلك", evidenceEmpty: "عندما أحسب شيئاً أو أتحقق من قائمة رسمية، تظهر التفاصيل هنا.",
    asked: "سألت",
    p: {
      unsupported: "الصوت يعمل في متصفح كروم أو إيدج. يمكنك الكتابة بدلاً من ذلك.",
      blocked: "الميكروفون محظور. اسمح به من شريط العنوان، أو اكتب بدلاً من ذلك.",
      too_short: "استمر في الضغط على الزر أثناء الكلام.",
      nothing: "لم أسمع شيئاً. اضغط على الزر أثناء الكلام.",
      failed: "لم يعمل الميكروفون. حاول مرة أخرى أو اكتب.",
    },
    netError: "لم أستطع الوصول إلى المرشد الآن. حاول مرة أخرى.",
  },
};
