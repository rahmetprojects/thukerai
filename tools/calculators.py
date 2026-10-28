"""
tools/calculators.py - every money calculation the assistant is allowed to make
==============================================================================

WHAT IT DOES
    Deterministic, unit-tested arithmetic. The AI (if any) may CHOOSE a calculation and pass numbers the
    person gave, but it never does the arithmetic itself. Every function returns a `Calc` with:
        inputs        exactly what was used (so the result is reproducible)
        outputs       the numbers (AED, months, percent ...), rounded for speech
        formula       a one-line, human-readable formula
        assumptions   what the result takes for granted (no growth? constant spending? ...)
        limitations   what it can NOT tell you

THE CALCULATIONS (grouped by the 3 protection pillars)
    knowledge   savings_runway, longevity_stress, retirement_capital_needed, emergency_fund,
                inflation_purchasing_power, future_cost, compound_growth, deposit_vs_inflation,
                fee_impact, return_reconcile, investment_loss, concentration
    decisions   loan_cost, income_stress, debt_vs_savings, rate_change, premium_change, charges_total,
                remittance_compare, property_sale, family_support, monthly_budget
    scams       promised_return  (what "10% a month" really means)

INPUT RULES
    Amounts are AED. Rates are PERCENT per YEAR unless the name says otherwise (e.g. promised_return takes
    a period). Negative or absurd values raise CalcError with a plain message naming the field.
    Unknown is not zero: callers must not pass 0 for something the person did not tell us.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


class CalcError(ValueError):
    def __init__(self, field_name: str, message: str):
        super().__init__(f"{field_name}: {message}")
        self.field, self.message = field_name, message


@dataclass
class Calc:
    tool: str
    inputs: dict
    outputs: dict
    formula: str
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _num(name: str, v, *, lo: float = 0.0, hi: float = 1e10, allow_zero: bool = True) -> float:
    if v is None or isinstance(v, bool):
        raise CalcError(name, "is missing")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CalcError(name, f"is not a number ({v!r})")
    if math.isnan(x) or math.isinf(x):
        raise CalcError(name, "is not a finite number")
    if x < lo or x > hi or (not allow_zero and x == 0):
        raise CalcError(name, f"must be between {lo:g} and {hi:g}{'' if allow_zero else ' and not zero'} (got {x:g})")
    return x


def _r(x: float, nd: int = 0):
    return None if x is None else (round(x) if nd == 0 else round(x, nd))


def _months_until_empty(savings: float, first_month_gap: float, annual_return_pct: float = 0.0,
                        annual_inflation_pct: float = 0.0, monthly_income: float = 0.0,
                        first_year_loss_pct: float = 0.0, max_months: int = 1200) -> int | None:
    """Simulate month by month. Spending (gap + income) rises with inflation once a year; income is flat.
    Returns None if the money lasts more than max_months (100 years)."""
    bal = savings * (1 - first_year_loss_pct / 100)
    spending = first_month_gap + monthly_income
    r = (1 + annual_return_pct / 100) ** (1 / 12) - 1
    for m in range(1, max_months + 1):
        if m > 1 and (m - 1) % 12 == 0:
            spending *= 1 + annual_inflation_pct / 100
        bal = bal * (1 + r) - (spending - monthly_income)
        if bal < 0:
            return m
    return None


# ====================================================================== PILLAR 1: knowledge
def savings_runway(savings, monthly_spending, monthly_income=0, annual_inflation_pct=0, annual_return_pct=0,
                   current_age=None) -> Calc:
    s = _num("savings", savings, hi=1e9)
    sp = _num("monthly_spending", monthly_spending, hi=1e7, allow_zero=False)
    inc = _num("monthly_income", monthly_income or 0, hi=1e7)
    infl = _num("annual_inflation_pct", annual_inflation_pct or 0, hi=50)
    ret = _num("annual_return_pct", annual_return_pct or 0, lo=-50, hi=50)
    age = _num("current_age", current_age, lo=18, hi=110) if current_age is not None else None
    gap = sp - inc
    out: dict = {"monthly_shortfall": _r(gap), "monthly_spending": _r(sp), "monthly_income": _r(inc)}
    if gap <= 0:
        out.update(months_simple=None, covered_by_income=True, monthly_surplus=_r(-gap))
        return Calc("savings_runway", locals_inputs(savings=s, monthly_spending=sp, monthly_income=inc), out,
                    "monthly shortfall = spending - income; it is zero or negative, so savings are not being drawn down",
                    ["income and spending stay as stated"], ["A surplus today can turn into a shortfall if costs rise faster than income."])
    months = s / gap
    out.update(months_simple=_r(months, 1), years_simple=_r(months / 12, 1), covered_by_income=False)
    assumptions = ["spending and income stay exactly as stated", "no investment gains or losses", "no unexpected costs"]
    if infl or ret:
        m2 = _months_until_empty(s, gap, ret, infl, inc)
        out["months_with_assumptions"] = m2
        out["years_with_assumptions"] = None if m2 is None else _r(m2 / 12, 1)
        assumptions = [f"spending rises {infl:g}% a year (income does not)" if infl else "spending does not rise",
                       f"savings earn {ret:g}% a year, every year" if ret else "savings earn nothing",
                       "no unexpected costs"]
    if age is not None:
        out["money_runs_out_around_age"] = _r(age + (out.get("months_with_assumptions") or months) / 12, 0)
    return Calc("savings_runway", locals_inputs(savings=s, monthly_spending=sp, monthly_income=inc,
                annual_inflation_pct=infl, annual_return_pct=ret, current_age=age), out,
                "months = savings / (monthly spending - monthly income); with inflation/returns: simulated month by month",
                assumptions, ["A simple projection, not a guarantee or a retirement plan.",
                              "Medical costs, rent changes and family needs can shorten it."])


def longevity_stress(savings, monthly_spending, current_age, monthly_income=0, annual_inflation_pct=2.0,
                     target_ages=(85, 90, 95)) -> Calc:
    s = _num("savings", savings, hi=1e9)
    sp = _num("monthly_spending", monthly_spending, hi=1e7, allow_zero=False)
    age = _num("current_age", current_age, lo=40, hi=105)
    inc = _num("monthly_income", monthly_income or 0, hi=1e7)
    infl = _num("annual_inflation_pct", annual_inflation_pct, hi=30)
    gap = sp - inc
    scenarios = {"no growth, prices rising": dict(annual_return_pct=0),
                 "3% a year growth": dict(annual_return_pct=3),
                 "a 20% fall in the first year, then 3% growth": dict(annual_return_pct=3, first_year_loss_pct=20)}
    rows = []
    for label, kw in scenarios.items():
        m = None if gap <= 0 else _months_until_empty(s, gap, annual_inflation_pct=infl, monthly_income=inc, **kw)
        runs_out = None if m is None else round(age + m / 12, 1)
        rows.append({"scenario": label, "money_lasts_years": None if m is None else round(m / 12, 1),
                     "runs_out_at_age": runs_out,
                     "short_by_years_at": {str(t): (round(t - runs_out, 1) if runs_out and runs_out < t else 0)
                                           for t in target_ages}})
    return Calc("longevity_stress", locals_inputs(savings=s, monthly_spending=sp, monthly_income=inc, current_age=age,
                annual_inflation_pct=infl), {"scenarios": rows, "monthly_shortfall": _r(gap)},
                "month-by-month simulation of savings under three return paths, spending rising with inflation",
                [f"prices rise {infl:g}% a year", "income stays flat", "the three return paths are illustrations"],
                ["Not a forecast: real returns, costs and lifespan are uncertain.",
                 "A fall early in retirement does more damage than the same fall later (sequence risk)."])


def retirement_capital_needed(monthly_spending, monthly_income, years, annual_inflation_pct=0, annual_return_pct=0) -> Calc:
    sp = _num("monthly_spending", monthly_spending, hi=1e7, allow_zero=False)
    inc = _num("monthly_income", monthly_income or 0, hi=1e7)
    y = _num("years", years, lo=1, hi=60)
    g, r = _num("annual_inflation_pct", annual_inflation_pct or 0, hi=30) / 100, \
        _num("annual_return_pct", annual_return_pct or 0, lo=-20, hi=30) / 100
    gap_year = max(0.0, (sp - inc) * 12)
    if abs(r - g) < 1e-9:
        need = gap_year * y / (1 + r)
    else:
        need = gap_year / (1 + r) * (1 - ((1 + g) / (1 + r)) ** y) / (1 - (1 + g) / (1 + r))
    return Calc("retirement_capital_needed", locals_inputs(monthly_spending=sp, monthly_income=inc, years=y,
                annual_inflation_pct=g * 100, annual_return_pct=r * 100),
                {"yearly_gap_today": _r(gap_year), "capital_needed": _r(need), "years": _r(y)},
                "present value of a yearly gap that grows with inflation, discounted at the assumed return",
                ["the gap is paid once a year", "constant inflation and return"],
                ["A rough target, not advice. Taxes, health costs and longevity change it."])


def emergency_fund(monthly_expenses, months=(3, 6, 12)) -> Calc:
    e = _num("monthly_expenses", monthly_expenses, hi=1e7, allow_zero=False)
    return Calc("emergency_fund", {"monthly_expenses": e, "months": list(months)},
                {f"{m}_months": _r(e * m) for m in months},
                "emergency buffer = monthly essential expenses x number of months",
                ["expenses stay as stated"],
                ["An emergency buffer is NOT the money needed for a whole retirement."])


def inflation_purchasing_power(amount, cumulative_pct=None, annual_pct=None, years=None, period_label="") -> Calc:
    a = _num("amount", amount, hi=1e10, allow_zero=False)
    if cumulative_pct is None:
        ap, y = _num("annual_pct", annual_pct, lo=-10, hi=50), _num("years", years, lo=0.1, hi=60)
        cum = ((1 + ap / 100) ** y - 1) * 100
    else:
        cum = _num("cumulative_pct", cumulative_pct, lo=-50, hi=500)
    real = a / (1 + cum / 100)
    return Calc("inflation_purchasing_power", locals_inputs(amount=a, cumulative_pct=_r(cum, 2), annual_pct=annual_pct,
                years=years, period=period_label),
                {"buys_what_this_bought_before": _r(real), "purchasing_power_lost": _r(a - real),
                 "price_rise_pct": _r(cum, 2)},
                "today's value in earlier money = amount / (1 + cumulative price rise)",
                ["national average prices"], ["Your own costs (especially rent) may have risen faster or slower."])


def future_cost(monthly_cost, annual_inflation_pct, years) -> Calc:
    c = _num("monthly_cost", monthly_cost, hi=1e7, allow_zero=False)
    i = _num("annual_inflation_pct", annual_inflation_pct, lo=-5, hi=30)
    y = _num("years", years, lo=0.5, hi=50)
    f = c * (1 + i / 100) ** y
    return Calc("future_cost", locals_inputs(monthly_cost=c, annual_inflation_pct=i, years=y),
                {"monthly_cost_then": _r(f), "increase": _r(f - c)},
                "future cost = cost today x (1 + yearly inflation) ^ years",
                [f"prices rise {i:g}% every year"], ["Past inflation does not predict future inflation."])


def compound_growth(principal, annual_rate_pct, years, monthly_contribution=0) -> Calc:
    p = _num("principal", principal, hi=1e10)
    r = _num("annual_rate_pct", annual_rate_pct, lo=-50, hi=50)
    y = _num("years", years, lo=0.1, hi=60)
    c = _num("monthly_contribution", monthly_contribution or 0, hi=1e7)
    m = (1 + r / 100) ** (1 / 12) - 1
    n = round(y * 12)
    fv = p * (1 + m) ** n + (c * (((1 + m) ** n - 1) / m) if m else c * n)
    paid = p + c * n
    return Calc("compound_growth", locals_inputs(principal=p, annual_rate_pct=r, years=y, monthly_contribution=c),
                {"final_value": _r(fv), "total_paid_in": _r(paid), "growth": _r(fv - paid)},
                "value = principal x (1+monthly rate)^months + contributions compounded monthly",
                [f"exactly {r:g}% every year, no fees, no withdrawals"],
                ["Real investment returns vary and can be negative. This is an illustration, not a promise."])


def deposit_vs_inflation(amount, deposit_rate_pct, inflation_pct, years=1, annual_fee_pct=0) -> Calc:
    a = _num("amount", amount, hi=1e10, allow_zero=False)
    d = _num("deposit_rate_pct", deposit_rate_pct, hi=30)
    i = _num("inflation_pct", inflation_pct, lo=-10, hi=50)
    y = _num("years", years, lo=0.1, hi=30)
    f = _num("annual_fee_pct", annual_fee_pct or 0, hi=10)
    nominal = a * (1 + (d - f) / 100) ** y
    real = nominal / (1 + i / 100) ** y
    real_rate = ((1 + (d - f) / 100) / (1 + i / 100) - 1) * 100
    return Calc("deposit_vs_inflation", locals_inputs(amount=a, deposit_rate_pct=d, inflation_pct=i, years=y, annual_fee_pct=f),
                {"interest_earned": _r(nominal - a), "balance_after": _r(nominal), "real_return_pct_per_year": _r(real_rate, 2),
                 "purchasing_power_change": _r(real - a)},
                "real return = (1 + deposit rate - fees) / (1 + inflation) - 1",
                ["rate and inflation stay the same for the whole period"],
                ["Check whether the rate is fixed or variable, and any early-withdrawal penalty."])


def fee_impact(principal, years, gross_return_pct, annual_fee_pct) -> Calc:
    p = _num("principal", principal, hi=1e10, allow_zero=False)
    y = _num("years", years, lo=1, hi=50)
    g = _num("gross_return_pct", gross_return_pct, lo=-20, hi=30)
    f = _num("annual_fee_pct", annual_fee_pct, hi=10)
    without, with_fee = p * (1 + g / 100) ** y, p * (1 + (g - f) / 100) ** y
    return Calc("fee_impact", locals_inputs(principal=p, years=y, gross_return_pct=g, annual_fee_pct=f),
                {"value_without_fee": _r(without), "value_with_fee": _r(with_fee), "cost_of_fees": _r(without - with_fee),
                 "share_of_growth_lost_pct": _r(100 * (without - with_fee) / (without - p), 1) if without > p else None},
                "value = principal x (1 + return - fee) ^ years, compared with no fee",
                [f"a steady {g:g}% gross return every year"], ["Entry, exit and performance fees would add to this."])


def return_reconcile(invested, years, stated_rate_pct, actual_balance, annual_fee_pct=0) -> Calc:
    p = _num("invested", invested, hi=1e10, allow_zero=False)
    y = _num("years", years, lo=0.1, hi=50)
    s = _num("stated_rate_pct", stated_rate_pct, lo=-20, hi=50)
    b = _num("actual_balance", actual_balance, hi=1e10)
    f = _num("annual_fee_pct", annual_fee_pct or 0, hi=10)
    expected = p * (1 + s / 100) ** y
    implied = ((b / p) ** (1 / y) - 1) * 100
    return Calc("return_reconcile", locals_inputs(invested=p, years=y, stated_rate_pct=s, actual_balance=b, annual_fee_pct=f),
                {"expected_at_stated_rate": _r(expected), "actual_balance": _r(b), "gap": _r(expected - b),
                 "actual_rate_pct_per_year": _r(implied, 2),
                 "expected_after_known_fees": _r(p * (1 + (s - f) / 100) ** y) if f else None},
                "expected = invested x (1 + stated rate) ^ years; actual rate = (balance / invested) ^ (1/years) - 1",
                ["no withdrawals or top-ups during the period"],
                ["A quoted rate may be a target, not a guarantee; ask the provider what it means."])


def investment_loss(amount, loss_pct) -> Calc:
    a = _num("amount", amount, hi=1e10, allow_zero=False)
    l = _num("loss_pct", loss_pct, lo=0.01, hi=99.99)
    rem = a * (1 - l / 100)
    return Calc("investment_loss", locals_inputs(amount=a, loss_pct=l),
                {"remaining": _r(rem), "money_lost": _r(a - rem), "gain_needed_to_recover_pct": _r(100 * (a / rem - 1), 1)},
                "remaining = amount x (1 - loss); recovery needs a gain of loss / (1 - loss)",
                [], ["A paper loss becomes real only if you sell; but holding is not automatically right either."])


def concentration(holding_value, total_investable) -> Calc:
    h = _num("holding_value", holding_value, hi=1e10, allow_zero=False)
    t = _num("total_investable", total_investable, hi=1e10, allow_zero=False)
    if h > t:
        raise CalcError("total_investable", "is smaller than the single holding; please check both amounts")
    share = 100 * h / t
    level = "very high" if share >= 50 else "high" if share >= 25 else "notable" if share >= 10 else "modest"
    return Calc("concentration", locals_inputs(holding_value=h, total_investable=t),
                {"share_pct": _r(share, 1), "level": level, "if_it_fell_30_pct_you_lose": _r(h * 0.3),
                 "that_is_pct_of_everything": _r(share * 0.3, 1)},
                "share = holding / total investable savings",
                ["level bands: 10% notable, 25% high, 50% very high (rules of thumb, not regulation)"],
                ["Also consider whether your job or pension depends on the same company or sector."])


# ====================================================================== PILLAR 2: decisions
def flat_rate_loan(principal, flat_rate_pct, years) -> Calc:
    """A FLAT-rate quote: interest on the original amount for the whole term. Also finds the equivalent reducing rate."""
    p = _num("principal", principal, hi=1e9, allow_zero=False)
    r = _num("flat_rate_pct", flat_rate_pct, hi=60)
    y = _num("years", years, lo=0.25, hi=35)
    n = round(y * 12)
    interest = p * r / 100 * y
    pay = (p + interest) / n
    lo, hi = 0.0, 1.0                                       # monthly reducing rate giving the same payment (bisection)
    for _ in range(200):
        m = (lo + hi) / 2
        test = p / n if m == 0 else p * m / (1 - (1 + m) ** -n)
        lo, hi = (m, hi) if test < pay else (lo, m)
    return Calc("flat_rate_loan", locals_inputs(principal=p, flat_rate_pct=r, years=y),
                {"monthly_payment": _r(pay), "number_of_payments": n, "total_repaid": _r(p + interest),
                 "total_interest": _r(interest), "equivalent_reducing_rate_pct": _r(lo * 12 * 100, 1)},
                "interest = P x flat rate x years; payment = (P + interest) / months; equivalent reducing rate: the "
                "yearly rate that gives the same payment on a reducing balance",
                ["no fees included", "equal monthly payments"],
                ["Ask the lender for the APR, which should include fees."])


def loan_cost(principal, annual_rate_pct, years, upfront_fees=0, quoted_monthly_payment=None) -> Calc:
    p = _num("principal", principal, hi=1e9, allow_zero=False)
    r = _num("annual_rate_pct", annual_rate_pct, hi=60)
    y = _num("years", years, lo=0.25, hi=35)
    fees = _num("upfront_fees", upfront_fees or 0, hi=1e8)
    n, m = round(y * 12), r / 100 / 12
    pay = p / n if m == 0 else p * m / (1 - (1 + m) ** -n)
    total = pay * n
    out = {"monthly_payment": _r(pay), "number_of_payments": n, "total_repaid": _r(total),
           "total_interest": _r(total - p), "total_cost_of_borrowing": _r(total - p + fees)}
    if quoted_monthly_payment is not None:
        q = _num("quoted_monthly_payment", quoted_monthly_payment, hi=1e8, allow_zero=False)
        out["quoted_monthly_payment"] = _r(q)
        out["quoted_minus_calculated"] = _r(q - pay)
    return Calc("loan_cost", locals_inputs(principal=p, annual_rate_pct=r, years=y, upfront_fees=fees), out,
                "payment = P x m / (1 - (1+m)^-n), m = yearly rate / 12, n = months (reducing balance)",
                ["a reducing-balance rate; a FLAT rate quote costs much more than the same number as a reducing rate"],
                ["Insurance, processing fees and early-settlement charges may be extra."])


def income_stress(monthly_income, essential_expenses, new_payment=0, income_drop_pct=0) -> Calc:
    i = _num("monthly_income", monthly_income, hi=1e8, allow_zero=False)
    e = _num("essential_expenses", essential_expenses, hi=1e8)
    pmt = _num("new_payment", new_payment or 0, hi=1e8)
    drop = _num("income_drop_pct", income_drop_pct or 0, hi=100)
    left_now = i - e - pmt
    i2 = i * (1 - drop / 100)
    left_after = i2 - e - pmt
    return Calc("income_stress", locals_inputs(monthly_income=i, essential_expenses=e, new_payment=pmt, income_drop_pct=drop),
                {"left_each_month_now": _r(left_now), "income_after_drop": _r(i2), "left_each_month_after_drop": _r(left_after),
                 "payment_share_of_income_pct": _r(100 * pmt / i, 1)},
                "left = income - essentials - payment, now and after the income drop",
                ["essentials stay the same"], ["Healthcare costs often rise with age; leave room for them."])


def debt_vs_savings(debt, debt_rate_pct, savings, savings_rate_pct, monthly_spending, buffer_months=6,
                    early_settlement_fee_pct=0) -> Calc:
    d = _num("debt", debt, hi=1e9, allow_zero=False)
    dr = _num("debt_rate_pct", debt_rate_pct, hi=60)
    s = _num("savings", savings, hi=1e10)
    sr = _num("savings_rate_pct", savings_rate_pct or 0, hi=30)
    sp = _num("monthly_spending", monthly_spending, hi=1e7, allow_zero=False)
    bm = _num("buffer_months", buffer_months, lo=1, hi=24)
    fee = _num("early_settlement_fee_pct", early_settlement_fee_pct or 0, hi=10)
    buffer = sp * bm

    def scen(pay: float) -> dict:
        pay = min(pay, d, s)
        fee_cost = pay * fee / 100
        left_debt, left_cash = d - pay, s - pay - fee_cost
        return {"pay_now": _r(pay), "fee_paid": _r(fee_cost), "debt_left": _r(left_debt), "cash_left": _r(left_cash),
                "cash_covers_months": _r(left_cash / sp, 1) if left_cash > 0 else 0,
                "interest_cost_next_year": _r(left_debt * dr / 100),
                "interest_earned_next_year": _r(max(left_cash, 0) * sr / 100),
                "net_cost_next_year": _r(left_debt * dr / 100 - max(left_cash, 0) * sr / 100 + fee_cost)}
    partial = max(0.0, s - buffer)
    return Calc("debt_vs_savings", locals_inputs(debt=d, debt_rate_pct=dr, savings=s, savings_rate_pct=sr,
                monthly_spending=sp, buffer_months=bm, early_settlement_fee_pct=fee),
                {"keep_everything": scen(0), "pay_part_keep_buffer": scen(partial), "pay_all_possible": scen(d),
                 "buffer_target": _r(buffer)},
                "one-year comparison: interest paid on remaining debt minus interest earned on remaining cash, plus fees",
                [f"a {bm:g}-month emergency buffer", "rates stay the same for a year"],
                ["Paying debt is certain saving; keeping cash protects you from emergencies. Both matter."])


def rate_change(balance, current_rate_pct, changes_pct_points=(-0.5, -0.25, 0.25, 0.5)) -> Calc:
    b = _num("balance", balance, hi=1e10, allow_zero=False)
    r = _num("current_rate_pct", current_rate_pct, hi=30)
    rows = [{"change_points": c, "new_rate_pct": _r(max(0.0, r + c), 2), "interest_per_year": _r(b * max(0.0, r + c) / 100),
             "difference_per_year": _r(b * (max(0.0, r + c) - r) / 100)} for c in changes_pct_points]
    return Calc("rate_change", locals_inputs(balance=b, current_rate_pct=r),
                {"interest_per_year_now": _r(b * r / 100), "scenarios": rows},
                "yearly interest = balance x rate", ["the account's rate is variable and follows the change fully"],
                ["Fixed-term deposits keep their rate until they mature; banks may not pass changes on in full."])


def premium_change(old_premium, new_premium, payments_per_year=12) -> Calc:
    o = _num("old_premium", old_premium, hi=1e8, allow_zero=False)
    n = _num("new_premium", new_premium, hi=1e8, allow_zero=False)
    k = _num("payments_per_year", payments_per_year, lo=1, hi=52)
    return Calc("premium_change", locals_inputs(old_premium=o, new_premium=n, payments_per_year=k),
                {"increase_pct": _r(100 * (n / o - 1), 1), "extra_per_year": _r((n - o) * k)},
                "increase = new / old - 1", [],
                ["Compare cover, exclusions and waiting periods too: a cheaper policy may cover less."])


def charges_total(amounts, recurring_monthly=True) -> Calc:
    vals = [_num(f"amounts[{i}]", a, hi=1e7) for i, a in enumerate(amounts or [])]
    if not vals:
        raise CalcError("amounts", "no charges given")
    total = sum(vals)
    return Calc("charges_total", {"amounts": vals, "recurring_monthly": recurring_monthly},
                {"total": _r(total, 2), "per_year_if_monthly": _r(total * 12, 2) if recurring_monthly else None},
                "sum of the charges (x 12 if they repeat monthly)", [],
                ["Unrecognised charges should be checked with the bank through its official number."])


def remittance_compare(amount_aed, offers) -> Calc:
    a = _num("amount_aed", amount_aed, hi=1e8, allow_zero=False)
    rows = []
    for i, o in enumerate(offers or []):
        rate = _num(f"offers[{i}].rate", o.get("rate"), hi=1e6, allow_zero=False)
        fee = _num(f"offers[{i}].fee_aed", o.get("fee_aed", 0), hi=1e6)
        rows.append({"provider": o.get("name", f"offer {i + 1}"), "rate": rate, "fee_aed": fee,
                     "recipient_gets": round((a - fee) * rate, 2)})
    if len(rows) < 1:
        raise CalcError("offers", "need at least one offer with a rate")
    best = max(r["recipient_gets"] for r in rows)
    for r in rows:
        r["less_than_best"] = round(best - r["recipient_gets"], 2)
        r["less_than_best_per_year_if_monthly"] = round(12 * (best - r["recipient_gets"]), 2)
    return Calc("remittance_compare", {"amount_aed": a, "offers": offers}, {"offers": rows},
                "recipient gets = (amount - fee) x exchange rate", ["quotes are current at the time given"],
                ["Rates move constantly; compare quotes taken at the same time."])


def property_sale(sale_price, mortgage_balance=0, selling_cost_pct=2, annual_rent_if_sold=None) -> Calc:
    p = _num("sale_price", sale_price, hi=1e10, allow_zero=False)
    mb = _num("mortgage_balance", mortgage_balance or 0, hi=1e10)
    sc = _num("selling_cost_pct", selling_cost_pct, hi=15)
    net = p - mb - p * sc / 100
    out = {"net_proceeds": _r(net), "selling_costs": _r(p * sc / 100)}
    if annual_rent_if_sold:
        rent = _num("annual_rent_if_sold", annual_rent_if_sold, hi=1e8, allow_zero=False)
        out["years_of_rent_covered"] = _r(net / rent, 1)
    return Calc("property_sale", locals_inputs(sale_price=p, mortgage_balance=mb, selling_cost_pct=sc,
                annual_rent_if_sold=annual_rent_if_sold), out,
                "net = price - mortgage - selling costs; years of rent = net / yearly rent",
                [f"selling costs of {sc:g}% (agency and transfer fees vary by emirate)"],
                ["Rent usually rises over time; your home also gives housing security that money alone does not."])


def family_support(savings, monthly_spending_without_support, monthly_support, monthly_income=0, reduced_support=None) -> Calc:
    s = _num("savings", savings, hi=1e10)
    sp = _num("monthly_spending_without_support", monthly_spending_without_support, hi=1e7, allow_zero=False)
    sup = _num("monthly_support", monthly_support, hi=1e7)
    inc = _num("monthly_income", monthly_income or 0, hi=1e7)
    red = sup / 2 if reduced_support is None else _num("reduced_support", reduced_support, hi=1e7)

    def months(support):
        gap = sp + support - inc
        return None if gap <= 0 else round(s / gap, 1)
    return Calc("family_support", locals_inputs(savings=s, monthly_spending_without_support=sp, monthly_support=sup,
                monthly_income=inc, reduced_support=red),
                {"months_with_current_support": months(sup), "months_with_reduced_support": months(red),
                 "months_without_support": months(0), "support_per_year": _r(sup * 12)},
                "months = savings / (spending + support - income)", ["no growth or inflation"],
                ["How much to help family is your decision; this only shows the effect on your savings."])


def monthly_budget(income_items: dict, expense_items: dict) -> Calc:
    inc = {k: _num(f"income.{k}", v, hi=1e8) for k, v in (income_items or {}).items()}
    exp = {k: _num(f"expenses.{k}", v, hi=1e8) for k, v in (expense_items or {}).items()}
    ti, te = sum(inc.values()), sum(exp.values())
    biggest = sorted(exp.items(), key=lambda kv: -kv[1])[:3]
    return Calc("monthly_budget", {"income": inc, "expenses": exp},
                {"total_income": _r(ti), "total_expenses": _r(te), "surplus_or_shortfall": _r(ti - te),
                 "largest_costs": [{"item": k, "amount": _r(v)} for k, v in biggest]},
                "surplus = total income - total expenses", ["amounts are monthly"], [])


# ====================================================================== PILLAR 3: scams
def promised_return(rate_pct, period="month", example_amount=10000) -> Calc:
    r = _num("rate_pct", rate_pct, lo=0.01, hi=1000)
    per_year = {"day": 365, "week": 52, "month": 12, "year": 1}.get(period)
    if per_year is None:
        raise CalcError("period", "must be day, week, month or year")
    ex = _num("example_amount", example_amount, hi=1e9, allow_zero=False)
    annual = ((1 + r / 100) ** per_year - 1) * 100
    level = ("extreme" if annual >= 100 else "very high" if annual >= 25 else "high" if annual >= 12 else "ordinary")
    return Calc("promised_return", locals_inputs(rate_pct=r, period=period, example_amount=ex),
                {"compounded_per_year_pct": _r(annual, 0 if annual >= 100 else 1),
                 "example_after_one_year": _r(ex * (1 + annual / 100)), "multiple_after_one_year": _r(1 + annual / 100, 1),
                 "level": level},
                f"yearly = (1 + {r:g}%) ^ {per_year} - 1", ["the promised rate is paid and reinvested every period"],
                ["A promise is not evidence. Returns this high are a classic sign of fraud, and no legitimate "
                 "investment can guarantee them."])


def locals_inputs(**kw) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


CALCULATORS = {f.__name__: f for f in (
    savings_runway, longevity_stress, retirement_capital_needed, emergency_fund, inflation_purchasing_power,
    future_cost, compound_growth, deposit_vs_inflation, fee_impact, return_reconcile, investment_loss, concentration,
    loan_cost, income_stress, debt_vs_savings, rate_change, premium_change, charges_total, remittance_compare,
    property_sale, family_support, monthly_budget, promised_return)}
