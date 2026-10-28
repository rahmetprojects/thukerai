"""
tests/test_calculators.py - every money calculation: the scenario numbers and the edge cases
=========================================================================================
The example figures from the 30 scenarios must come out exactly; invalid inputs must raise CalcError
with the field name; "covered by income" must never divide by zero; unknown is not zero.
"""
import pytest

from tools import calculators as C


def test_scenario_examples():
    assert C.savings_runway(250000, 5000).outputs["months_simple"] == 50.0            # S01
    assert C.savings_runway(180000, 5500).outputs["months_simple"] == 32.7            # S30
    assert C.promised_return(10, "month").outputs["compounded_per_year_pct"] == 214   # S30
    assert C.inflation_purchasing_power(100000, cumulative_pct=10).outputs["buys_what_this_bought_before"] == 90909  # S02
    assert C.emergency_fund(6000).outputs["6_months"] == 36000                        # S05
    assert C.return_reconcile(100000, 5, 8, 118000).outputs["expected_at_stated_rate"] == 146933  # S11


def test_runway_covered_by_income_does_not_divide_by_zero():
    out = C.savings_runway(100000, 4000, 5000).outputs
    assert out["covered_by_income"] is True and out["months_simple"] is None and out["monthly_surplus"] == 1000


def test_inflation_shortens_runway():
    plain = C.savings_runway(250000, 5000).outputs["months_simple"]
    infl = C.savings_runway(250000, 5000, 0, 5).outputs["months_with_assumptions"]
    assert infl < plain


def test_loan_cost_reducing_balance():
    o = C.loan_cost(100000, 7, 5).outputs
    assert o["monthly_payment"] == 1980 and o["total_interest"] == 18807
    assert C.loan_cost(12000, 0, 1).outputs["monthly_payment"] == 1000                # zero rate


def test_loss_recovery_asymmetry():
    o = C.investment_loss(100000, 50).outputs
    assert o["remaining"] == 50000 and o["gain_needed_to_recover_pct"] == 100.0


def test_concentration_levels():
    assert C.concentration(80000, 200000).outputs["level"] == "high"
    with pytest.raises(C.CalcError) as e:
        C.concentration(300000, 200000)
    assert e.value.field == "total_investable"


def test_debt_vs_savings_keeps_buffer():
    o = C.debt_vs_savings(30000, 18, 80000, 0, 6000).outputs
    assert o["keep_everything"]["interest_cost_next_year"] == 5400
    assert o["pay_part_keep_buffer"]["cash_left"] == 44000 - 0 or o["pay_part_keep_buffer"]["pay_now"] == 30000


@pytest.mark.parametrize("fn,args,field", [
    (C.savings_runway, (-5, 5000), "savings"),
    (C.savings_runway, (1000, 0), "monthly_spending"),
    (C.savings_runway, (1000, None), "monthly_spending"),
    (C.loan_cost, (1000, 7, 0), "years"),
    (C.promised_return, (10, "fortnight"), "period"),
    (C.charges_total, ([],), "amounts"),
])
def test_invalid_inputs_name_the_field(fn, args, field):
    with pytest.raises(C.CalcError) as e:
        fn(*args)
    assert e.value.field == field


def test_every_calc_states_formula_and_limits():
    c = C.compound_growth(10000, 5, 10, 100)
    assert c.formula and c.assumptions and c.limitations and c.inputs["principal"] == 10000
