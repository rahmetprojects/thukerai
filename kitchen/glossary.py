"""
kitchen/glossary.py - the financial terms the assistant can explain, each tied to the datasets it relates to
==========================================================================================================

Each entry:
    term        how the term is shown ("passported fund")
    match       regex that finds the term in an answer or a question
    plain       the explanation in plain English (2 to 3 sentences, no jargon)
    plain_ar    a short Arabic explanation where available (not yet reviewed by a native speaker)
    example     one concrete example
    datasets    registry ids where the term shows up in the official data (shown with counts and pages)
Order matters: earlier entries are offered first when several terms appear in one answer.
The explainer NEVER explains a term that is not here: it says it has no explanation instead of improvising.
"""
from __future__ import annotations

import re

TERMS: dict[str, dict] = {
    "cma_warning": {
        "term": "CMA warning", "match": r"\bCMA warnings?\b|\bwarnings? (list|published)\b",
        "plain": "A CMA warning is a public notice from the UAE Capital Market Authority telling investors not to deal "
                 "with a company, person or website, because it is not licensed or authorised to offer those financial "
                 "services in the UAE.",
        "plain_ar": "تحذير الهيئة إشعار علني من هيئة سوق رأس المال ينبه المستثمرين إلى عدم التعامل مع جهة غير مرخصة.",
        "example": "A website offering \"guaranteed\" forex profits to UAE residents without a licence can be named in a warning.",
        "datasets": ["cma_warnings"]},
    "licence": {
        "term": "CMA licence", "match": r"\blicen[cs]ed? compan|\bCMA-licensed\b|\blicen[cs]e\b",
        "plain": "A CMA licence allows a company to carry out specific financial activities in the UAE mainland. It "
                 "covers only the activities listed for that company, and it does not guarantee returns or protect you "
                 "from losses.",
        "plain_ar": "ترخيص الهيئة يسمح للشركة بمزاولة أنشطة مالية محددة فقط، ولا يضمن الأرباح.",
        "example": "A firm licensed only for \"introduction\" may introduce you to other licensed firms but may not manage your money.",
        "datasets": ["cma_licensed_companies", "cma_licensed_activities"]},
    "licensed_activity": {
        "term": "licensed activity", "match": r"\b(licensed|permitted) activit|\bintroduction\b|\bactivities\b",
        "plain": "Every CMA licence lists the activities the firm may do, such as introduction, brokerage or portfolio "
                 "management. A firm licensed for one activity is not allowed to do another.",
        "example": "\"Introduction\" means introducing clients to other licensed firms, not handling their money.",
        "datasets": ["cma_licensed_activities"]},
    "lookalike": {
        "term": "lookalike website", "match": r"\blooks like the website\b|\blookalike\b",
        "plain": "A lookalike website copies a real, licensed firm's name with a slightly different address, to borrow its "
                 "reputation. Always use the website listed on the official record.",
        "example": "\"4tglobal-invest.net\" copying the licensed firm whose official site is \"4tglobal.ae\".",
        "datasets": ["cma_licensed_companies"]},
    "finfluencer": {
        "term": "finfluencer", "match": r"\bfinfluencers?\b|\bfinancial influencers?\b",
        "plain": "A finfluencer is someone who promotes financial products or services on social media. In the UAE they "
                 "need CMA authorisation to do this. Authorisation allows them to promote; it is not a recommendation of "
                 "what they promote.",
        "plain_ar": "المؤثر المالي شخص يروج لمنتجات مالية عبر وسائل التواصل، ويحتاج إلى تصريح من الهيئة.",
        "example": "An Instagram account posting stock tips should appear on the CMA's authorised list.",
        "datasets": ["cma_finfluencers"]},
    "passported_fund": {
        "term": "passported fund", "match": r"\bpassport(ed|ing)? funds?\b|\bpassported\b",
        "plain": "A passported fund is set up in one of the UAE's financial free zones (ADGM or DIFC) and is allowed to be "
                 "marketed to investors in the UAE mainland under the CMA's passporting arrangement.",
        "example": "A fund domiciled in ADGM that a mainland bank is allowed to offer you.",
        "datasets": ["cma_passported_funds"]},
    "local_fund": {
        "term": "local fund", "match": r"\blocal (investment )?funds?\b|\bpublic funds?\b|\bprivate funds?\b",
        "plain": "A local fund is an investment fund set up in the UAE mainland and registered with the CMA. Its value goes "
                 "up and down, and it charges fees.",
        "example": "A mutual fund registered with the CMA as a public fund.",
        "datasets": ["cma_local_funds"]},
    "eosg_fund": {
        "term": "end-of-service fund", "match": r"\bend[- ]of[- ]service\b|\bgratuity\b|\bEOSB\b|\bEOSG\b",
        "plain": "End-of-service funds invest the end-of-service benefits that employers set aside for employees under the "
                 "UAE's savings scheme for end-of-service benefits. The CMA lists the approved funds.",
        "example": "A bank-run fund your employer pays your gratuity into each month.",
        "datasets": ["cma_eosg_funds"]},
    "violation": {
        "term": "violation", "match": r"\bviolations?\b|\bpenalt(y|ies)\b|\bfines?\b",
        "plain": "A violation is a breach of capital-market rules. The CMA publishes the penalties it imposes, such as fines, "
                 "with the rule that was broken.",
        "example": "A financial penalty for trading on information that was not yet public.",
        "datasets": ["cma_violations"]},
    "pjsc": {
        "term": "PJSC", "match": r"\bPJSCs?\b|\bpublic joint[- ]stock\b",
        "plain": "A PJSC (public joint-stock company) is a company whose shares are offered to the public, usually listed on "
                 "a UAE exchange (ADX or DFM).",
        "example": "A listed bank or property developer.",
        "datasets": ["cma_auditors", "cma_assembly_organizers"]},
    "general_assembly": {
        "term": "general assembly", "match": r"\bgeneral assembl(y|ies)\b|\bAGM\b",
        "plain": "A general assembly is a company's shareholders' meeting, where shareholders vote on matters such as "
                 "dividends and board members. CMA-registered organisers run these meetings for PJSCs.",
        "example": "The yearly meeting where shareholders approve the dividend.",
        "datasets": ["cma_assembly_organizers"]},
    "in_kind": {
        "term": "in-kind contribution", "match": r"\bin[- ]kind\b|\bappraisers?\b",
        "plain": "An in-kind contribution is an asset, such as land or shares, given to a company or fund instead of cash. "
                 "A CMA-registered appraiser values it.",
        "example": "A founder contributing a building to a new company in exchange for shares.",
        "datasets": ["cma_appraisers"]},
    "auditor": {
        "term": "registered auditor", "match": r"\bauditors?\b|\baudit firms?\b",
        "plain": "Audit firms registered with the CMA may audit public joint-stock companies and investment funds. "
                 "Registration is not a licence to manage money.",
        "example": "The firm that signs a listed company's yearly accounts.",
        "datasets": ["cma_auditors"]},
    "compounding": {
        "term": "compounding", "match": r"\bcompound(ed|ing)?\b|\ba year\b.*\bif compounded\b",
        "plain": "Compounding means earning returns on earlier returns. A monthly rate compounds into a much larger yearly "
                 "figure, which is why promises of a high monthly return are so unrealistic.",
        "example": "15% a month, compounded, turns 10,000 into more than 50,000 in a year: a promise no real investment can keep.",
        "datasets": []},
    "guaranteed_return": {
        "term": "guaranteed return", "match": r"\bguarantee(d)?\b|\bpromised return\b",
        "plain": "Real investments can lose money, so nobody can promise a fixed high return. A guarantee of high, regular "
                 "returns is one of the most common signs of investment fraud.",
        "example": "\"Guaranteed 15% a month, no risk.\"",
        "datasets": ["cma_warnings"]},
    "other_regulators": {
        "term": "other regulators", "match": r"\bDFSA\b|\bFSRA\b|\bVARA\b|\bCentral Bank\b",
        "plain": "The CMA does not cover everything. Firms in the DIFC are regulated by the DFSA, firms in ADGM by the FSRA, "
                 "banks and insurers by the Central Bank, and virtual-asset firms in Dubai by VARA. Not being on a CMA list "
                 "can simply mean the firm is regulated elsewhere.",
        "example": "A DIFC wealth manager appears on the DFSA register, not the CMA's.",
        "datasets": []},
    "inflation": {
        "term": "inflation", "match": r"\binflation\b|\bconsumer price\b|\bCPI\b",
        "plain": "Inflation means prices in general are rising. When it is 3%, something that cost 100 dirhams last year "
                 "costs about 103 now, so cash that earns less than inflation loses buying power.",
        "example": "Rent and school fees rising faster than your savings earn.",
        "datasets": ["fcsc_cpi_annual"]},
}

# General investing terms (no dataset of their own); explanations from the voice guide's glossary.
for _k, _term, _match in (("nav", "NAV", r"\bNAV\b|\bnet asset value\b"), ("etf", "ETF", r"\bETFs?\b"),
                          ("sukuk", "sukuk", r"\bsukuk\b"), ("bond", "bond", r"\bbonds?\b"),
                          ("dividend", "dividend", r"\bdividends?\b"), ("flat rate", "flat rate", r"\bflat rate\b"),
                          ("reducing rate", "reducing rate", r"\breducing rate\b"), ("apr", "APR", r"\bAPR\b"),
                          ("annuity", "annuity", r"\bannuit(y|ies)\b"), ("expense ratio", "expense ratio", r"\bexpense ratio\b"),
                          ("diversif", "diversification", r"\bdiversif")):
    from agents.workflows import GLOSSARY as _G
    TERMS[_k.replace(" ", "_")] = {"term": _term, "match": _match, "plain": _G[_k], "example": "",
                                   "datasets": ["cma_local_funds", "cma_passported_funds"] if _k in ("nav", "etf", "expense ratio") else []}

_COMPILED = {k: re.compile(v["match"], re.I) for k, v in TERMS.items()}


def find_terms(text: str) -> list[str]:
    """Glossary keys whose term appears in the text, in glossary order."""
    return [k for k, rx in _COMPILED.items() if rx.search(text or "")]


def by_name(text: str) -> str | None:
    """The glossary key a person is asking about ("what is a passported fund?"), or None."""
    hits = find_terms(text)
    return hits[0] if hits else None
