"""
agents/workflows.py - what to calculate, what to check, and what to say, for each intent
========================================================================================

WHAT IT DOES
    One function per intent (agents/intents.py). The coordinator calls it ONLY when every required fact
    is known. A workflow:
        1. runs deterministic calculations (tools/calculators.py) and official-data lookups
           (tools/retrieval.py, tools/inflation.py) - never guesses a number;
        2. lets CURRENT DATA CHANGE THE ANSWER: e.g. the latest official inflation figure is fed into the
           savings runway, a licence's permitted activities and website are compared with what was said;
        3. writes the answer from the results: short spoken sentences (`say`), the working shown on screen
           (`points`), safe next steps, assumptions and limitations - all collected in an Answer.

    When official data is unavailable, the workflow SAYS SO and, if it continues, labels the stand-in value
    as an assumption (e.g. "I used 2% a year as an illustration because the official figures are not loaded").

    The text is plain on purpose: one idea per sentence, digits for numbers (read aloud by the browser), no
    jargon without a one-line explanation, never "you should buy/sell", never "safe" or "guaranteed".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re
from tools import calculators as C
from tools.inflation import Inflation, InflationError
from tools.retrieval import Retrieval

from .speak import aed, date_text, join, months_text, pct

FALLBACK_INFLATION = 2.0


@dataclass
class Answer:
    say: list[str] = field(default_factory=list)          # the spoken reply, sentence by sentence
    points: list[str] = field(default_factory=list)       # "how I worked this out" (shown, not spoken)
    next_steps: list[str] = field(default_factory=list)
    calcs: list[dict] = field(default_factory=list)       # Calc.as_dict() of every calculation run
    checks: list[dict] = field(default_factory=list)      # entity checks / dataset searches
    data: list[dict] = field(default_factory=list)        # dataset states used (snapshot ids, dates)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    quick_replies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)        # names of tools run (for the trace and evals)


class Kit:
    """The tools a workflow may use, plus helpers that record what was used into the Answer."""

    def __init__(self, retrieval: Retrieval, inflation: Inflation):
        self.r, self.inf = retrieval, inflation

    def calc(self, a: Answer, fn, *args, **kw) -> C.Calc:
        c = fn(*args, **kw)
        a.calcs.append(c.as_dict())
        a.tools.append(c.tool)
        for x in c.assumptions:
            if x not in a.assumptions:
                a.assumptions.append(x)
        for x in c.limitations:
            if x not in a.limitations:
                a.limitations.append(x)
        return c

    def recent_inflation(self, a: Answer, years: int = 5, category: str = "ALL") -> tuple[float, str]:
        """(average yearly %, sentence naming the source) - or a labelled illustrative value if unavailable."""
        try:
            info = self.inf.available()
            hi = info["years"][1]
            ch = self.inf.change(hi - years, hi, category)
            a.tools.append("inflation")
            a.calcs.append({"tool": "inflation", "inputs": {"start_year": hi - years, "end_year": hi, "category": category},
                            "outputs": {k: ch[k] for k in ("total_price_rise_pct", "average_per_year_pct")},
                            "formula": "chained FCSC annual CPI: index(end) / index(start) - 1",
                            "assumptions": [], "limitations": ch["limitations"]})
            self._data(a, "fcsc_cpi_annual")
            return ch["average_per_year_pct"], (f"UAE inflation averaged {pct(ch['average_per_year_pct'])} a year from "
                                                f"{hi - years} to {hi}, according to the FCSC")
        except (InflationError, KeyError, IndexError):
            a.assumptions.append(f"official inflation data was not available, so {pct(FALLBACK_INFLATION)} a year is used "
                                 "as an illustration only")
            return FALLBACK_INFLATION, (f"the official inflation figures are not loaded right now, so I used "
                                        f"{pct(FALLBACK_INFLATION)} a year as an illustration")

    def _data(self, a: Answer, dataset_id: str) -> dict:
        st = self.r.dataset_state(dataset_id)
        if not any(d["dataset_id"] == dataset_id for d in a.data):
            a.data.append({k: st[k] for k in ("dataset_id", "name", "publisher", "source_page", "status", "snapshot_id",
                                               "fetched_at", "rows", "last_error")})
        return st


def _v(s: dict, k: str, default=None):
    return s.get(k, default)


# =============================================================================== PILLAR 1
def retirement_readiness(s: dict, k: Kit) -> Answer:
    a = Answer()
    sav, sp, inc = s["savings"], s["monthly_spending"], s.get("monthly_income", 0) or 0
    base = k.calc(a, C.savings_runway, sav, sp, inc)
    o = base.outputs
    if o["covered_by_income"]:
        a.say.append(f"Your regular income of {aed(inc)} a month covers your spending of {aed(sp)}, so on these numbers "
                     f"your savings of {aed(sav)} are not being used up.")
        infl, src = k.recent_inflation(a)
        fc = k.calc(a, C.future_cost, sp, infl, 10)
        a.say.append(f"But {src}. At that pace, {aed(sp)} of spending today becomes about {aed(fc.outputs['monthly_cost_then'])} "
                     "a month in 10 years, so check whether your income rises too.")
    else:
        inc_txt = f"and {aed(inc)} of regular income" if inc else "and no other income"
        a.say.append(f"With {aed(sav)} saved, spending {aed(sp)} a month {inc_txt}, your savings would cover "
                     f"{months_text(o['months_simple'])}, if nothing at all changed.")
        infl, src = k.recent_inflation(a)
        age = s.get("retire_age") or s.get("current_age")
        adj = k.calc(a, C.savings_runway, sav, sp, inc, infl, 0, age)
        ao = adj.outputs
        a.say.append(f"{src[0].upper() + src[1:]}. If your costs keep rising like that, it is closer to "
                     f"{months_text(ao['months_with_assumptions'])}.")
        if age:
            a.say.append(f"Starting at age {int(age)}, the money would run out around age {ao['money_runs_out_around_age']}.")
            need = k.calc(a, C.retirement_capital_needed, sp, inc, max(1, 90 - int(age)), infl, 0)
            a.points.append(f"To cover the gap until age 90 with prices rising {pct(infl)} a year and no investment growth, "
                            f"you would need about {aed(need.outputs['capital_needed'])} in total.")
    a.say.append("This is a simple starting point, not a full retirement plan: emergencies, health costs and investment "
                 "gains or losses can all change it.")
    a.next_steps += ["Tell me about any other savings, debts, or whether you own your home.",
                     "I can work out how much regular income would close the gap."]
    a.quick_replies = ["What if I spend less?", "How much income would close the gap?", "What about living to 90?"]
    return a


def how_much_needed(s: dict, k: Kit) -> Answer:
    a = Answer()
    sp, inc, age = s["monthly_spending"], s.get("monthly_income", 0) or 0, s["current_age"]
    ef = k.calc(a, C.emergency_fund, sp)
    a.say.append(f"At {aed(sp)} a month, six months of expenses is {aed(ef.outputs['6_months'])}. That is a common emergency "
                 "buffer, but it is not the money needed to fund a whole retirement.")
    infl, src = k.recent_inflation(a)
    start = s.get("retire_age") or age
    years = max(1, 90 - int(start))
    need = k.calc(a, C.retirement_capital_needed, sp, inc, years, infl, 0)
    gap = need.outputs["yearly_gap_today"]
    if gap <= 0:
        a.say.append(f"Your regular income of {aed(inc)} covers that spending today, so the main need is the emergency buffer "
                     "and room for rising costs.")
    else:
        a.say.append(f"To cover a gap of {aed(gap)} a year from age {int(start)} to 90, with prices rising as they have recently, "
                     f"you would need roughly {aed(need.outputs['capital_needed'])}, before any investment growth.")
        a.points.append(f"{src}; that rate is assumed to continue.")
    a.say.append("Living longer, or higher health costs, would raise that figure; steady investment growth could lower it.")
    a.quick_replies = ["What if I live to 95?", "What if I retire later?"]
    return a


def longevity(s: dict, k: Kit) -> Answer:
    a = Answer()
    infl, src = k.recent_inflation(a)
    targets = tuple(sorted(set((s.get("target_ages") or []) + [90]))) if s.get("target_ages") else (85, 90, 95)
    st = k.calc(a, C.longevity_stress, s["savings"], s["monthly_spending"], s["current_age"], s.get("monthly_income", 0) or 0,
                infl, targets)
    rows = st.outputs["scenarios"]
    if st.outputs["monthly_shortfall"] <= 0:
        a.say.append("Your regular income covers your spending today, so your savings are not being drawn down on these numbers.")
    else:
        for r in rows:
            when = f"around age {r['runs_out_at_age']:.0f}" if r["runs_out_at_age"] else "beyond age 100"
            a.say.append(f"With {r['scenario']}, the money would last until {when}.")
        worst = rows[2]
        goal = max(targets)
        if worst["runs_out_at_age"] and worst["runs_out_at_age"] < goal:
            short = worst["short_by_years_at"][str(goal)]
            a.say.append(f"In the toughest case you would be about {short:.0f} years short of age {goal}.")
        elif worst["runs_out_at_age"] is None or worst["runs_out_at_age"] >= goal:
            a.say.append(f"On these assumptions, even the toughest case reaches age {goal}.")
    a.say.append(f"These are illustrations, not a forecast. {src[0].upper() + src[1:]}, and I assumed costs keep rising that way.")
    a.next_steps.append("Dependable income for life, such as a pension or annuity, is what protects against living longer than expected.")
    a.quick_replies = ["What if I spend 500 less a month?", "What is an annuity?"]
    return a


def purchasing_power(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt = s.get("amount") or s.get("savings")
    cat = s.get("cpi_category") or "ALL"
    yrs = s.get("years_list") or []
    try:
        info = k.inf.available()
        hi = info["years"][1]
        start = yrs[0] if yrs and info["years"][0] <= yrs[0] < hi else hi - 5
        ch = k.inf.change(start, hi, cat)
        last = k.inf.change(hi - 1, hi, cat)
        k._data(a, "fcsc_cpi_annual")
        a.tools.append("inflation")
        a.calcs.append({"tool": "inflation", "inputs": {"start_year": start, "end_year": hi, "category": cat},
                        "outputs": {"total_price_rise_pct": ch["total_price_rise_pct"], "average_per_year_pct": ch["average_per_year_pct"],
                                    f"rise_in_{hi}_pct": last["total_price_rise_pct"]},
                        "formula": "chained FCSC annual CPI: index(end) / index(start) - 1", "assumptions": [],
                        "limitations": ch["limitations"]})
        what = "UAE prices" if cat == "ALL" else f"UAE {ch['category']} prices"
        a.say.append(f"Official FCSC figures: {what} rose {pct(last['total_price_rise_pct'])} in {hi}, and "
                     f"{pct(ch['total_price_rise_pct'])} in total from {start} to {hi}, about {pct(ch['average_per_year_pct'])} a year.")
        if amt:
            pp = k.calc(a, C.inflation_purchasing_power, amt, cumulative_pct=ch["total_price_rise_pct"], period_label=f"{start}-{hi}")
            a.say.append(f"So {aed(amt)} today buys roughly what {aed(pp.outputs['buys_what_this_bought_before'])} bought in {start}: "
                         f"about {aed(pp.outputs['purchasing_power_lost'])} of buying power lost.")
        yearly = last["total_price_rise_pct"]
    except (InflationError, KeyError, IndexError):
        yearly, hi = FALLBACK_INFLATION, None
        a.say.append("I could not load the official inflation figures right now, so I can only illustrate.")
        a.assumptions.append(f"{pct(FALLBACK_INFLATION)} inflation used as an illustration only")
    if amt:
        dv = k.calc(a, C.deposit_vs_inflation, amt, s.get("deposit_rate_pct") or 0, yearly, 1)
        rate_txt = f"earning {pct(s['deposit_rate_pct'])}" if s.get("deposit_rate_pct") else "earning no interest"
        change = dv.outputs["purchasing_power_change"]
        a.say.append(f"Kept in cash {rate_txt} for a year with prices rising {pct(yearly)}"
                     f"{' as they did in ' + str(hi) if hi else ''}, it would "
                     + (f"lose about {aed(-change)} of buying power." if change < 0 else f"gain about {aed(change)} of buying power."))
        a.say.append("Cash still has an important job: money you may need soon, or for emergencies, should stay accessible. "
                     "The question is how much you need to keep that way.")
        a.quick_replies = ["What about food prices?", "And rent?", "My account pays some interest"]
    else:
        a.say.append("Tell me an amount, for example your savings, and I'll show what it buys now compared with before.")
        a.quick_replies = ["What about food prices?", "And rent?", "I have 100,000 in savings"]
    a.limitations.append("National average prices: your own costs, especially rent, may rise faster or slower.")
    a.say.append("These are national averages; your own costs may rise faster or slower.")
    return a


def inflation_news(s: dict, k: Kit) -> Answer:
    a = Answer()
    try:
        info = k.inf.available()
        hi = info["years"][1]
        y1, y0 = k.inf.change(hi - 1, hi), k.inf.change(hi - 2, hi - 1)
        hou = k.inf.change(hi - 1, hi, "HOU")
        k._data(a, "fcsc_cpi_annual")
        a.tools.append("inflation")
        a.calcs.append({"tool": "inflation", "inputs": {"years": [hi - 2, hi - 1, hi]},
                        "outputs": {f"rise_{hi - 1}": y0["total_price_rise_pct"], f"rise_{hi}": y1["total_price_rise_pct"],
                                    f"housing_rise_{hi}": hou["total_price_rise_pct"]},
                        "formula": "year-on-year change of the chained FCSC CPI", "assumptions": [], "limitations": y1["limitations"]})
        a.say.append("Not necessarily. Falling inflation means prices are rising more slowly; it does not mean prices go back down.")
        a.say.append(f"Official FCSC figures: prices rose {pct(y0['total_price_rise_pct'])} in {hi - 1} and "
                     f"{pct(y1['total_price_rise_pct'])} in {hi}. Either way, the level of prices is higher than before.")
        a.say.append(f"Housing and utilities, often the biggest cost, rose {pct(hou['total_price_rise_pct'])} in {hi}. "
                     "If your rent goes up, your budget gets tighter even when the national rate falls.")
    except (InflationError, KeyError, IndexError):
        a.say.append("Falling inflation means prices are rising more slowly, not that they go back down. I could not load the "
                     "official figures right now to show the latest numbers.")
    a.quick_replies = ["Is my money keeping up with prices?", "Update my budget for rising costs"]
    return a


def income_sufficiency(s: dict, k: Kit) -> Answer:
    a = Answer()
    inc, sp = s["monthly_income"], s["monthly_spending"]
    b = k.calc(a, C.monthly_budget, {"regular income": inc}, {"monthly spending": sp})
    diff = b.outputs["surplus_or_shortfall"]
    if diff < 0:
        a.say.append(f"On these numbers there is a shortfall of {aed(-diff)} a month, or {aed(-diff * 12)} a year, "
                     "that would have to come from savings or other income.")
    else:
        a.say.append(f"On these numbers {aed(inc)} covers your spending of {aed(sp)} with {aed(diff)} a month to spare.")
    infl, src = k.recent_inflation(a)
    fc = k.calc(a, C.future_cost, sp, infl, 5)
    a.say.append(f"{src[0].upper() + src[1:]}. If that continues, spending of {aed(sp)} becomes about "
                 f"{aed(fc.outputs['monthly_cost_then'])} a month in 5 years.")
    a.say.append("The biggest drivers are usually housing, health insurance and support for family. Do you rent, or own your home?")
    a.quick_replies = ["I rent", "I own my home", "I also support family"]
    return a


def fund_vs_bank(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt, r = s["amount"], s["fund_return_pct"]
    a.say.append(f"A {pct(r)} return last year is past performance; it does not tell us what the fund will do next year.")
    g = k.calc(a, C.compound_growth, amt, r, 1)
    a.say.append(f"On {aed(amt)}, a year like last year would add about {aed(g.outputs['growth'])}, before fees.")
    loss = k.calc(a, C.investment_loss, amt, 15)
    a.say.append(f"But funds can also fall: a 15% bad year would leave {aed(loss.outputs['remaining'])}. Cash in a bank account "
                 "rarely does that, but it usually earns less.")
    if s.get("annual_fee_pct"):
        fi = k.calc(a, C.fee_impact, amt, 10, 6, s["annual_fee_pct"])
        a.say.append(f"Fees matter too: a {pct(s['annual_fee_pct'])} yearly fee would cost about {aed(fi.outputs['cost_of_fees'])} "
                     "over 10 years at a steady 6% return.")
    else:
        a.say.append("Ask whether the 12-month figure is before or after fees, which share class and currency it is, and the yearly fee.")
    if s.get("entity_name"):
        res = [k.r.search(d, s["entity_name"]) for d in ("cma_local_funds", "cma_passported_funds")]
        a.checks += res
        a.tools.append("search_funds")
        hit = next((x for x in res if x["match"] in ("exact", "normalized", "domain")), None)
        for x in res:
            k._data(a, x["dataset_id"])
        a.say.append(f"I found “{hit['candidates'][0]['name']}” on the CMA's {hit['name']} list. Registration is not a promise about returns."
                     if hit else f"I did not find “{s['entity_name']}” on the CMA's local or passported fund lists; ask which "
                     "regulator registered it.")
    a.say.append("The key question is when you will need this money: money needed within a year or two is exposed if the fund falls.")
    a.quick_replies = ["I'll need it in 2 years", "I won't need it for 10 years"]
    return a


def return_gap(s: dict, k: Kit) -> Answer:
    a = Answer()
    rc = k.calc(a, C.return_reconcile, s["invested"], s["years"], s["stated_rate_pct"], s["actual_balance"], s.get("annual_fee_pct") or 0)
    o = rc.outputs
    a.say.append(f"At {pct(s['stated_rate_pct'])} a year, {aed(s['invested'])} over {s['years']:g} years would grow to about "
                 f"{aed(o['expected_at_stated_rate'])}. Your statement shows {aed(o['actual_balance'])}, a gap of {aed(o['gap'])}.")
    a.say.append(f"That balance means your money actually grew about {pct(o['actual_rate_pct_per_year'], 2)} a year.")
    a.say.append("The usual reasons are fees, a quoted rate that was a target rather than a guarantee, or a rate that applies only "
                 "to part of the money. Before adding more, ask the provider in writing for every fee and how the rate is worked out.")
    a.quick_replies = ["What fees should I ask about?"]
    return a


def concentration(s: dict, k: Kit) -> Answer:
    a = Answer()
    cc = k.calc(a, C.concentration, s["holding_value"], s["total_investable"])
    o = cc.outputs
    a.say.append(f"{aed(s['holding_value'])} out of {aed(s['total_investable'])} is {pct(o['share_pct'])} of your savings in one "
                 f"company. That is a {o['level']} concentration.")
    a.say.append(f"If that company's shares fell 30%, you would lose about {aed(o['if_it_fell_30_pct_you_lose'])}, which is "
                 f"{pct(o['that_is_pct_of_everything'])} of everything you have.")
    a.say.append("Recent gains do not show what comes next. It also matters if your job or pension depends on the same company or sector.")
    a.quick_replies = ["What does diversification mean?", "My job is in the same sector"]
    return a


def asset_hype(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt, total = s["amount"], s["total_investable"]
    cc = k.calc(a, C.concentration, amt, total)
    loss = k.calc(a, C.investment_loss, amt, 20)
    a.say.append("Popularity does not make something suitable for retirement money, and a recent rise does not tell us what happens next.")
    a.say.append(f"Putting {aed(amt)} in would be {pct(cc.outputs['share_pct'])} of your savings. If its price fell 20%, you would "
                 f"lose {aed(loss.outputs['money_lost'])}.")
    a.say.append("Gold pays no interest or dividends, and buying and selling has costs. Could you still pay your bills if it fell?")
    a.limitations.append("This assistant does not have live gold or market prices, so it cannot describe the recent trend.")
    a.quick_replies = ["What are the costs of buying gold?", "How much should stay in cash?"]
    return a


GLOSSARY = {
    "inflation": "Inflation means prices in general are rising. When it is 3%, something that cost 100 dirhams last year costs about 103 now.",
    "compound": "Compounding means earning returns on your earlier returns too, so money grows faster over time. It works against you with debt.",
    "annuity": "An annuity is a product where you pay a lump sum and receive a regular income, often for life. Check fees, inflation protection and what happens if you die early.",
    "sukuk": "Sukuk are Islamic finance certificates, similar to bonds, that pay returns from an underlying asset instead of interest. They can still lose value.",
    "etf": "An ETF is a fund traded on a stock exchange like a share. It usually tracks an index and often has low fees, but its price goes up and down.",
    "bond": "A bond is a loan to a government or company that pays interest. Its value can fall if rates rise or the borrower gets into trouble.",
    "dividend": "A dividend is a share of a company's profit paid to shareholders. Companies can cut or stop dividends.",
    "flat rate": "A flat rate charges interest on the original loan amount for the whole term, so it costs much more than the same number quoted as a reducing rate.",
    "reducing rate": "A reducing rate charges interest only on what you still owe, so the interest falls as you repay.",
    "apr": "The APR shows the yearly cost of borrowing including some fees, so you can compare loans more fairly.",
    "diversif": "Diversification means spreading money across different investments, so one bad result does not hurt you too much.",
    "expense ratio": "The expense ratio is a fund's yearly fee as a percentage of your money. 1.5% a year on 100,000 dirhams is 1,500 dirhams every year.",
    "nav": "NAV, the net asset value, is the value of one unit of a fund. It goes up and down with the fund's investments.",
    "fund": "A fund pools money from many people and invests it for them. Its value can rise or fall, and it charges fees.",
    "interest": "Interest is what a bank pays you for savings, or charges you for a loan, usually quoted per year.",
}


def explain_term(s: dict, k: Kit) -> Answer:
    a = Answer()
    text = (s.get("_text") or "").lower()
    for short, full in (("flat", "flat rate"), ("reducing", "reducing rate"), ("compounding", "compound")):
        if re.search(rf"\b{short}\b", text) and full not in text:
            text += " " + full
    hits = [v for kk, v in GLOSSARY.items() if kk in text]
    a.say += hits[:2] or ["I can explain terms like inflation, compounding, annuity, sukuk, ETF, bond, dividend, flat or reducing "
                          "rate and expense ratio. Which one would you like?"]
    a.tools.append("glossary")
    a.quick_replies = ["What is a flat rate?", "What is an annuity?", "What is inflation?"]
    return a


# =============================================================================== PILLAR 2
def deposit_offer(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt, rate = s["amount"], s["deposit_rate_pct"]
    try:
        hi = k.inf.available()["years"][1]
        infl = k.inf.change(hi - 1, hi)["total_price_rise_pct"]
        src = f"prices rose {pct(infl)} in {hi} (FCSC)"
        k._data(a, "fcsc_cpi_annual")
        a.tools.append("inflation")
    except (InflationError, KeyError, IndexError):
        infl, src = FALLBACK_INFLATION, f"I used {pct(FALLBACK_INFLATION)} inflation as an illustration because official figures are not loaded"
        a.assumptions.append(src)
    dv = k.calc(a, C.deposit_vs_inflation, amt, rate, infl, s.get("years") or 1)
    o = dv.outputs
    a.say.append(f"On {aed(amt)}, {pct(rate)} a year would earn about {aed(o['interest_earned'])} in interest in the first year.")
    a.say.append(f"Compared with inflation, since {src}, that is a real gain of about {pct(o['real_return_pct_per_year'], 2)} a year."
                 if o["real_return_pct_per_year"] >= 0 else
                 f"Since {src}, that would still lose buying power, about {pct(-o['real_return_pct_per_year'], 2)} a year.")
    a.say.append("Before deciding, check four things: is the rate fixed or variable, how long the money is locked up, the penalty "
                 "for early withdrawal, and any fees. A higher rate is not better if you might need the money early.")
    a.next_steps.append("Compare the offer with the same product at one or two other banks, quoted on the same terms.")
    a.quick_replies = ["What if rates change?", "I might need the money in 6 months"]
    return a


def market_drop(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt, lp, sp = s["amount"], s["loss_pct"], s["monthly_spending"]
    l = k.calc(a, C.investment_loss, amt, lp)
    o = l.outputs
    a.say.append(f"A {pct(lp)} fall on {aed(amt)} is {aed(o['money_lost'])}, leaving {aed(o['remaining'])}. To get back to "
                 f"{aed(amt)} it would need to rise {pct(o['gain_needed_to_recover_pct'])}.")
    a.say.append("Selling after a fall locks in the loss; holding something unsuitable can also hurt. Nobody can tell you what "
                 "prices will do tomorrow, including me.")
    if s.get("savings"):
        r = k.calc(a, C.savings_runway, s["savings"], sp, s.get("monthly_income", 0) or 0)
        a.say.append(f"Your cash of {aed(s['savings'])} covers {months_text(r.outputs['months_simple'])} of spending, so you may not "
                     "be forced to sell at a low price.")
    else:
        a.say.append(f"The key question is how many months of your {aed(sp)} monthly spending you can cover from cash, without "
                     "selling these investments while prices are low. How much cash do you have?")
        a.quick_replies.append("I have 50,000 in cash")
    a.next_steps.append("If this money is needed within a year or two, discuss the mix with a licensed adviser rather than reacting to headlines.")
    return a


def debt_vs_savings(s: dict, k: Kit) -> Answer:
    a = Answer()
    d = k.calc(a, C.debt_vs_savings, s["debt"], s["debt_rate_pct"], s["savings"], s.get("savings_rate_pct") or 0, s["monthly_spending"])
    o = d.outputs
    keep, part, full = o["keep_everything"], o["pay_part_keep_buffer"], o["pay_all_possible"]
    a.say.append(f"Keeping the debt costs about {aed(keep['interest_cost_next_year'])} in interest over the next year at "
                 f"{pct(s['debt_rate_pct'])}.")
    a.say.append(f"Paying it all off would save that, but leave {aed(full['cash_left'])} in savings, which covers "
                 f"{full['cash_covers_months']:g} months of spending.")
    if part["pay_now"] and part["pay_now"] < s["debt"]:
        a.say.append(f"A middle way: pay {aed(part['pay_now'])} now and keep a 6-month emergency buffer of {aed(o['buffer_target'])}.")
    a.say.append("Check for early-settlement fees first. Clearing expensive debt is a certain saving, but keep enough cash for emergencies.")
    a.quick_replies = ["There is an early settlement fee", "What if I pay half?"]
    return a


def loan_offer(s: dict, k: Kit) -> Answer:
    a = Answer()
    if s.get("_for_family"):
        a.say.append("Because the loan is for someone else, you would be the one legally responsible for every payment, "
                     "even if their plans do not work out. Only borrow what you could afford to repay alone.")
    if s.get("_flat_rate"):
        fl = k.calc(a, C.flat_rate_loan, s["loan_principal"], s["debt_rate_pct"], s["years"])
        o = fl.outputs
        a.say.append(f"Borrowing {aed(s['loan_principal'])} at {pct(s['debt_rate_pct'])} flat over {s['years']:g} years means "
                     f"about {aed(o['monthly_payment'])} a month, {aed(o['total_repaid'])} in total, so "
                     f"{aed(o['total_interest'])} of interest. A flat rate is charged on the full amount for the whole term, "
                     f"so as a normal reducing rate this is about {pct(o['equivalent_reducing_rate_pct'])} a year, not "
                     f"{pct(s['debt_rate_pct'])}. Ask the bank for the APR.")
    else:
        lc = k.calc(a, C.loan_cost, s["loan_principal"], s["debt_rate_pct"], s["years"], 0, s.get("quoted_payment") or None)
        o = lc.outputs
        a.say.append(f"Borrowing {aed(s['loan_principal'])} at {pct(s['debt_rate_pct'])} over {s['years']:g} years means about "
                     f"{aed(o['monthly_payment'])} a month, {aed(o['total_repaid'])} in total, so {aed(o['total_interest'])} of interest.")
    if s.get("quoted_payment") and not s.get("_flat_rate") and abs(o.get("quoted_minus_calculated") or 0) > 0.03 * o["monthly_payment"]:
        a.say.append(f"Their quote of {aed(s['quoted_payment'])} differs from that: ask whether the rate is a flat rate, and what fees are included.")
    st = k.calc(a, C.income_stress, s["monthly_income"], s["monthly_spending"], o["monthly_payment"], 30)
    so = st.outputs
    def left(x):
        return f"{aed(x)} left each month" if x >= 0 else f"a shortfall of {aed(-x)} each month"
    a.say.append(f"After essentials and the payment you would have {left(so['left_each_month_now'])}. If your income "
                 f"fell by 30%, for example at retirement, you would have {left(so['left_each_month_after_drop'])}.")
    a.say.append("Look at the total cost and the full term, not only whether this month's payment fits.")
    a.quick_replies = ["What is a flat rate?", "What if I borrow less?"]
    return a


def unknown_charges(s: dict, k: Kit) -> Answer:
    a = Answer()
    ch = s["charges"] if isinstance(s["charges"], list) else [s["charges"]]
    c = k.calc(a, C.charges_total, ch, True)
    a.say.append(f"Those charges add up to {aed(c.outputs['total'])}. If they repeat every month, that is {aed(c.outputs['per_year_if_monthly'])} a year.")
    a.say.append("Small unknown charges are sometimes a test by fraudsters before a bigger one. Please don't ignore them.")
    a.say.append("Call your bank on the official number on your card, ask what each charge is, and dispute any you did not "
                 "authorise. Compare the others with the bank's published fee schedule.")
    a.next_steps.append("Never share your full card number, PIN or one-time codes when asking about a charge.")
    return a


def insurance_premium(s: dict, k: Kit) -> Answer:
    a = Answer()
    p = k.calc(a, C.premium_change, s["old_premium"], s["new_premium"])
    a.say.append(f"Your premium went up {pct(p.outputs['increase_pct'])}, about {aed(p.outputs['extra_per_year'])} more a year "
                 "if you pay monthly.")
    a.say.append("Before cancelling, find out what changed in the cover and compare like for like. For health insurance, check "
                 "waiting periods, pre-existing-condition terms and the hospital network.")
    a.say.append("Cancelling first and shopping later can leave a gap in cover. Arrange the new policy before ending the old one.")
    a.quick_replies = ["What questions should I ask the insurer?"]
    return a


def rate_change(s: dict, k: Kit) -> Answer:
    a = Answer()
    rc = k.calc(a, C.rate_change, s["amount"], s["deposit_rate_pct"])
    o = rc.outputs
    up = next(r for r in o["scenarios"] if r["change_points"] == 0.5)
    down = next(r for r in o["scenarios"] if r["change_points"] == -0.5)
    a.say.append(f"At {pct(s['deposit_rate_pct'])}, {aed(s['amount'])} earns about {aed(o['interest_per_year_now'])} a year.")
    a.say.append(f"If the rate moved half a percentage point, that becomes about {aed(down['interest_per_year'])} or "
                 f"{aed(up['interest_per_year'])}: a difference of about {aed(abs(up['difference_per_year']))} a year either way.")
    a.say.append("The dirham is pegged to the US dollar, so UAE rates tend to follow US rate decisions, but banks may not pass "
                 "changes on fully or at once. If your deposit is fixed for a term, its rate stays until it ends.")
    a.limitations.append("This assistant does not have live central-bank or bank rates.")
    a.quick_replies = ["My deposit is fixed for a year", "Compare with inflation"]
    return a


def sell_home(s: dict, k: Kit) -> Answer:
    a = Answer()
    ps = k.calc(a, C.property_sale, s["sale_price"], s.get("mortgage_balance") or 0, 2, s["annual_rent"])
    o = ps.outputs
    a.say.append(f"Selling for {aed(s['sale_price'])}, after the mortgage and about 2% selling costs, would leave roughly "
                 f"{aed(o['net_proceeds'])}.")
    a.say.append(f"If you then rent at {aed(s['annual_rent'])} a year, that money covers about {o['years_of_rent_covered']:g} years "
                 "of rent before any investment growth, and rents usually rise.")
    a.say.append("A rising market headline does not mean selling is right for you. Your home is also your place to live: the "
                 "plan needs to protect your housing security as well as your money.")
    a.limitations.append("This assistant does not have live property prices for your emirate or area.")
    a.quick_replies = ["What if I downsize instead?", "What would the money need to earn?"]
    return a


def remittance(s: dict, k: Kit) -> Answer:
    a = Answer()
    amt, rate, fee = s["remit_amount"], s["exchange_rate"], s.get("transfer_fee") or 0
    rc = k.calc(a, C.remittance_compare, amt, [{"name": "your current provider", "rate": rate, "fee_aed": fee},
                                                {"name": "a quote 1% better", "rate": round(rate * 1.01, 6), "fee_aed": fee}])
    cur, better = rc.outputs["offers"]
    a.say.append(f"Sending {aed(amt)} with a fee of {aed(fee)} at {rate:g} per dirham, your family receives about "
                 f"{cur['recipient_gets']:,.0f} in their currency.")
    a.say.append(f"A rate just 1% better would give them about {better['recipient_gets'] - cur['recipient_gets']:,.0f} more each "
                 f"time, roughly {cur['less_than_best_per_year_if_monthly']:,.0f} more a year.")
    a.say.append("I can't predict whether rates will move. What you can control is comparing the amount received, after fees, "
                 "from two or three licensed providers at the same moment.")
    a.limitations.append("This assistant does not have live exchange rates.")
    a.quick_replies = ["Another provider quoted me a rate"]
    return a


def medical_costs(s: dict, k: Kit) -> Answer:
    a = Answer()
    sav, sp, med, inc = s["savings"], s["monthly_spending"], s["monthly_medical"], s.get("monthly_income", 0) or 0
    base = k.calc(a, C.savings_runway, sav, sp, inc)
    try:
        hi = k.inf.available()["years"][1]
        mi = k.inf.change(hi - 5, hi, "MED")["average_per_year_pct"]
        k._data(a, "fcsc_cpi_annual")
        a.tools.append("inflation")
        src = f"medical prices rose {pct(mi)} a year on average from {hi - 5} to {hi} (FCSC)"
    except (InflationError, KeyError, IndexError):
        mi, src = 5.0, "I used 5% a year for medical costs as an illustration"
        a.assumptions.append(src)
    higher = k.calc(a, C.savings_runway, sav, sp + med * 0.5, inc)
    shock = k.calc(a, C.savings_runway, max(0, sav - 50000), sp, inc)
    if base.outputs["covered_by_income"]:
        a.say.append("Your income currently covers your spending, which gives you room to absorb higher health costs.")
    else:
        a.say.append(f"Today your savings cover {months_text(base.outputs['months_simple'])} of spending.")
        a.say.append(f"If your health costs of {aed(med)} a month rose by half, that falls to {months_text(higher.outputs['months_simple'])}. "
                     f"A one-off bill of {aed(50000)} would bring it to {months_text(shock.outputs['months_simple'])}.")
    a.say.append(f"For context, {src}. Check your policy for what is not covered, annual limits and co-payments, rather than "
                 "assuming insurance pays for everything.")
    a.quick_replies = ["What should I check in my policy?"]
    return a


def family_support(s: dict, k: Kit) -> Answer:
    a = Answer()
    f = k.calc(a, C.family_support, s["savings"], s["monthly_spending"], s["monthly_support"], s.get("monthly_income", 0) or 0)
    o = f.outputs
    a.say.append(f"Supporting your family with {aed(s['monthly_support'])} a month is {aed(o['support_per_year'])} a year.")
    a.say.append(f"With that support your savings cover {months_text(o['months_with_current_support'])}. With half the support it "
                 f"would be {months_text(o['months_with_reduced_support'])}.")
    a.say.append("How much to help is your decision. A clear, agreed support budget can protect both your family and your own retirement.")
    a.quick_replies = ["What if I support them for 2 more years only?"]
    return a


def job_loss(s: dict, k: Kit) -> Answer:
    a = Answer()
    sav, sp, inc = s["savings"], s["monthly_spending"], s.get("monthly_income", 0) or 0
    r = k.calc(a, C.savings_runway, sav, sp, inc)
    cut = k.calc(a, C.savings_runway, sav, sp * 0.9, inc)
    if r.outputs["covered_by_income"]:
        a.say.append("Your continuing income covers your essential spending, which gives you time.")
    else:
        a.say.append(f"Your savings of {aed(sav)} cover {months_text(r.outputs['months_simple'])} at {aed(sp)} a month. That is your time to plan.")
        a.say.append(f"Trimming spending by 10% would stretch it to {months_text(cut.outputs['months_simple'])}.")
    a.say.append("Check whether you can claim from the UAE's Involuntary Loss of Employment insurance if you were subscribed, and any "
                 "end-of-service gratuity owed to you. Eligibility depends on your status, so confirm with the official sources; "
                 "I can't confirm it for you.")
    a.next_steps += ["Pay essentials and urgent debts first.", "Avoid locking cash into investments while you have no income."]
    a.quick_replies = ["Which costs should I cut first?", "What is end-of-service gratuity?"]
    return a


def relocation(s: dict, k: Kit) -> Answer:
    a = Answer()
    sav, sp, inc = s["savings"], s["monthly_spending"], s.get("monthly_income", 0) or 0
    r = k.calc(a, C.savings_runway, sav, sp, inc)
    fx = k.calc(a, C.savings_runway, sav, sp * 1.2, inc)
    a.say.append(f"At {aed(sp)} a month in your home country, {aed(sav)} covers {months_text(r.outputs['months_simple'])}.")
    a.say.append(f"If the exchange rate moved 20% against the dirham, or costs there rose that much, it would be "
                 f"{months_text(fx.outputs['months_simple'])}. Currency is a real risk when you spend in one currency and save in another.")
    a.say.append("Also check healthcare cover there, tax and residency rules, and how your pension or end-of-service money will be "
                 "paid. Those need confirmation from the official authorities in both countries.")
    a.quick_replies = ["What if I keep some money in dirhams?"]
    return a


def deposit_protection(s: dict, k: Kit) -> Answer:
    a = Answer()
    a.say.append("Not necessarily. Being registered, licensed for an activity, or listed on a regulator's website are different "
                 "things, and none of them guarantees your money against loss.")
    a.say.append("The Capital Market Authority licenses firms for specific activities, such as brokerage, and its lists show which "
                 "activities each firm may do. Banks are licensed and supervised by the Central Bank of the UAE, not the Capital Market Authority.")
    a.say.append("What happens if a firm fails depends on the type of product: a bank deposit, a brokerage account where your "
                 "shares should be held separately, a fund, or a crypto platform are all treated differently. I don't have the "
                 "official rules on compensation limits, so I won't guess a figure. Ask the firm in writing, and check with the regulator.")
    if s.get("entity_name"):
        chk = k.r.check_entity(s["entity_name"], s.get("domain", ""))
        a.checks.append(chk)
        a.tools.append("check_entity")
        lic = chk["results"]["cma_licensed_companies"]
        k._data(a, "cma_licensed_companies")
        if lic["match"] in ("exact", "normalized", "domain"):
            top = lic["candidates"][0]
            acts = top.get("permitted_activities") or []
            a.say.append(f"“{top['name']}” is on the CMA's licensed list (licence {top['record_key']})"
                         + (f", permitted for: {join(acts[:3])}." if acts else "."))
    a.quick_replies = ["Check a company's licence", "What is a segregated account?"]
    return a


def benefit_programme(s: dict, k: Kit) -> Answer:
    a = Answer()
    a.say.append("I can't confirm that programme: I don't have official data on government benefits, and forwarded messages about "
                 "new benefits are sometimes scams that ask for your details or a fee.")
    a.say.append("Eligibility usually depends on nationality, age, residency and contribution history. Check only on the official "
                 "government website or app, for example the UAE government portal, or the General Pension and Social Security "
                 "Authority if you are an Emirati.")
    a.say.append("No genuine government programme asks you to pay a fee or share a bank code to receive a benefit.")
    a.tools.append("referral")
    a.quick_replies = ["The message asked for my details", "How do I spot a fake message?"]
    return a


# =============================================================================== PILLAR 3
def _verdict_sentences(chk: dict, k: Kit, a: Answer) -> list[str]:
    q = chk["query"]["name"] or chk["query"]["domain"]
    v = chk["verdict"]
    res = chk["results"]
    w = res["cma_warnings"]
    out: list[str] = []
    fetched = date_text((w.get("fetched_at") or "")[:10]) if w.get("fetched_at") else None
    if v == "warning_found":
        c = w["candidates"][0]
        how = {"exact": "an exact match", "normalized": "a match", "domain": "a matching website"}[c["match_type"]]
        out.append(f"I found an official Capital Market Authority warning with {how} for “{c['matched_value']}”, published on "
                   f"{date_text(c['date'])}.")
        stmt = (c["details"] or {}).get("statement")
        if stmt:
            out.append(f"The warning says: {stmt.split('. ')[0].rstrip('.')}.")
        out.append("Please do not send them money. A matching name is a strong warning sign, although it cannot prove the person "
                   "contacting you is that same entity; scammers also reuse names.")
    elif v == "possible_warning":
        c = w["candidates"][0]
        out.append(f"A similar name, “{c['matched_value']}”, appears on a Capital Market Authority warning published on "
                   f"{date_text(c['date'])}. It is not identical, so I can't say it is the same company.")
        out.append("Compare the exact legal name, website and phone number. Until you are sure, please do not send money.")
    elif v == "licensed_record_found":
        c = res["cma_licensed_companies"]["candidates"][0]
        acts = c.get("permitted_activities") or []
        out.append(f"“{c['name']}” appears on the Capital Market Authority's list of licensed companies, licence {c['record_key']}, "
                   f"status {c.get('status') or 'not stated'}.")
        if acts:
            out.append(f"It is licensed for: {join(acts[:3])}{' and more' if len(acts) > 3 else ''}. Check that what they offer you "
                       "is one of those activities.")
        site = (c.get("details") or {}).get("website")
        if site:
            out.append(f"Its official website on record is {site}. Contact the firm only through that, not through numbers or links "
                       "someone sent you.")
        out.append("A licence does not guarantee your money or any return, and it does not prove the person contacting you works there.")
    elif v == "authorised_finfluencer_found":
        c = res["cma_finfluencers"]["candidates"][0]
        out.append(f"“{c['name']}” is on the CMA's list of authorised finfluencers, registration {c['record_key']}, valid until "
                   f"{(c.get('details') or {}).get('valid_until_text') or 'a date not stated'}.")
        out.append("That allows them to promote, but it is not a recommendation of anything they promote. Check the account is really "
                   "theirs: the handle on the official list must match.")
    elif v == "registered_record_found":
        d = next(r for d, r in res.items() if r["match"] in ("exact", "normalized", "domain"))
        out.append(f"“{d['candidates'][0]['name']}” appears on the CMA's list of {d['name']}. Registration is not the same as a licence "
                   "to manage your money.")
    elif v == "could_not_check":
        out.append("I could not check the official lists right now because they are not available in my database. Please do not treat "
                   "that as a clean result.")
    else:
        searched = [r["name"] for r in res.values() if r["searched"]]
        out.append(f"I searched {len(searched)} official Capital Market Authority lists, including warnings and licensed companies, "
                   f"and found no record matching “{q}”.")
        out.append("Not finding it is not proof that it is genuine or licensed. It may be licensed by another regulator, such as the DFSA, "
                   "the FSRA, the Central Bank or VARA, or not licensed at all.")
        out.append("Ask them which regulator licensed them and their licence number, then check it on that regulator's own website.")
    for f in chk["flags"]:
        if f.startswith("website_mismatch"):
            out.append("Important: the website you gave is different from the website on the official licence record. Impersonating "
                       "real firms is a common scam.")
        elif f.startswith("published_violation"):
            out.append("The CMA has also published a penalty against a matching name.")
        elif f.startswith("conflict"):
            out.append("The name matches both a warning and a licensed firm; scammers often copy real firms' names, so verify through "
                       "the licensed firm's official contact details.")
    if fetched:
        out.append(f"The lists I used were copied from the CMA website on {fetched}.")
    return out


def check_entity(s: dict, k: Kit) -> Answer:
    a = Answer()
    chk = k.r.check_entity(s.get("entity_name", ""), s.get("domain", ""))
    a.checks.append(chk)
    a.tools.append("check_entity")
    for d in ("cma_warnings", "cma_licensed_companies", "cma_finfluencers"):
        k._data(a, d)
    a.say += _verdict_sentences(chk, k, a)
    a.limitations += chk["limitations"]
    a.quick_replies = ["They promise a high return", "They contacted me on WhatsApp", "Check another name"]
    return a


def suspicious_offer(s: dict, k: Kit) -> Answer:
    a = Answer()
    if s.get("savings") and s.get("monthly_spending"):
        r = k.calc(a, C.savings_runway, s["savings"], s["monthly_spending"], s.get("monthly_income", 0) or 0)
        a.say.append(f"First, your retirement money: {aed(s['savings'])} covers {months_text(r.outputs['months_simple'])} of spending "
                     f"at {aed(s['monthly_spending'])} a month, with no other income. That is your safety net, so it needs protecting.")
    pr = k.calc(a, C.promised_return, s["promised_rate_pct"], s.get("promised_period") or "month")
    o = pr.outputs
    period = s.get("promised_period") or "month"
    a.say.append(f"{'Second, the offer' if s.get('savings') and s.get('monthly_spending') else 'About the offer'}: "
                 f"{pct(s['promised_rate_pct'])} a {period}, if compounded, would be about "
                 f"{o['compounded_per_year_pct']:,.0f}% a year. A promise like that is not evidence that the return is real, and it "
                 "is a classic warning sign of fraud.")
    if s.get("entity_name") or s.get("domain"):
        chk = k.r.check_entity(s.get("entity_name", ""), s.get("domain", ""))
        a.checks.append(chk)
        a.tools.append("check_entity")
        for d in ("cma_warnings", "cma_licensed_companies"):
            k._data(a, d)
        a.say += _verdict_sentences(chk, k, a)[:3]
    else:
        a.say.append("If you tell me the company's name or website, I'll check it against the official Capital Market Authority "
                     "warnings and licensed companies.")
    if s.get("_channel"):
        a.say.append(f"Being approached on {s['_channel']} is another warning sign: licensed firms do not recruit "
                     "investors through private messages.")
    if s.get("_rush"):
        a.say.append("Being told to pay today is pressure, and pressure is how scammers stop people from checking.")
    a.say.append("My practical advice: don't transfer retirement savings under time pressure. Verify first, and talk to someone you trust.")
    a.say.append("If you have already sent money, tell me, because the next steps change.")
    a.quick_replies = ["I already sent money", "Check the company name", "They said it's guaranteed"]
    return a


def finfluencer_check(s: dict, k: Kit) -> Answer:
    a = Answer()
    res = k.r.search("cma_finfluencers", s["entity_name"])
    a.checks.append(res)
    a.tools.append("search_finfluencers")
    k._data(a, "cma_finfluencers")
    if not res["searched"]:
        a.say.append("I could not check the official finfluencer list right now because it is not loaded. Please don't treat that as a clean result.")
    elif res["match"] in ("exact", "normalized", "domain"):
        c = res["candidates"][0]
        a.say.append(f"“{c['name']}” is on the Capital Market Authority's list of authorised finfluencers, valid until "
                     f"{(c.get('details') or {}).get('valid_until_text') or 'a date not stated'}.")
        a.say.append("Authorisation lets them promote; it is not a recommendation. Make sure the account you follow matches the one on the official list.")
    else:
        a.say.append(f"I did not find “{s['entity_name']}” on the Capital Market Authority's list of authorised finfluencers.")
        a.say.append("Promoting investments on social media in the UAE needs CMA authorisation. Be very careful with tips, signal groups or "
                     "links from accounts that are not on the list.")
    a.limitations += res["limitations"]
    return a


def unsolicited_contact(s: dict, k: Kit) -> Answer:
    a = Answer()
    a.say.append("Being contacted out of the blue, on WhatsApp, social media or by a stranger's call, is one of the most "
                 "common ways investment scams start.")
    a.say.append("Genuine firms do not need you to move to WhatsApp or Telegram, join a signals group, click a link, install "
                 "an app or decide quickly.")
    if s.get("entity_name") or s.get("domain"):
        chk = k.r.check_entity(s.get("entity_name", ""), s.get("domain", ""))
        a.checks.append(chk)
        a.tools.append("check_entity")
        k._data(a, "cma_warnings")
        name = s.get("entity_name") or s.get("domain")
        if chk["verdict"] in ("warning_found", "possible_warning"):
            a.say.append(f"Remember that “{name}” matches a Capital Market Authority warning. Please stop contact and do not send money.")
        elif chk["verdict"] == "licensed_record_found":
            a.say.append(f"“{name}” is on the licensed list, but a WhatsApp message does not prove it is really them. Contact the "
                         "firm only through the website on the official record.")
        else:
            a.say.append(f"I found no record of “{name}” on the Capital Market Authority lists, which is one more reason to be careful.")
    a.say.append("Safe next steps: do not click links or share codes, do not send money, and if you want to know more, contact the "
                 "company through the details on the regulator's list, not the number that messaged you. You can also block and "
                 "report the number in WhatsApp.")
    a.tools.append("guidance")
    a.quick_replies = ["Check another company", "They promise a high return", "I already sent money"]
    return a


def scam_tips(s: dict, k: Kit) -> Answer:
    a = Answer()
    a.say += ["Five warning signs cover most investment scams.",
              "One: promises of guaranteed or very high returns, especially per month.",
              "Two: pressure to decide today, or to keep it secret from family or your bank.",
              "Three: requests for codes, remote access to your phone, or a fee before you can withdraw.",
              "Four: contact out of the blue through WhatsApp, social media or a stranger's phone call.",
              "Five: a firm that is not on the regulator's list, or a website that differs from the one on the list. I can check names for you."]
    a.tools.append("guidance")
    a.quick_replies = ["Check a company name", "Someone is offering me an investment"]
    return a


RUNNERS = {f.__name__: f for f in (
    retirement_readiness, how_much_needed, longevity, purchasing_power, inflation_news, income_sufficiency, fund_vs_bank,
    return_gap, concentration, asset_hype, explain_term, deposit_offer, market_drop, debt_vs_savings, loan_offer,
    unknown_charges, insurance_premium, rate_change, sell_home, remittance, medical_costs, family_support, job_loss,
    relocation, deposit_protection, benefit_programme, check_entity, suspicious_offer, finfluencer_check,
    unsolicited_contact, scam_tips)}
