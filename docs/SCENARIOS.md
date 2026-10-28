# The 30 scenarios: how each is handled

Every scenario is an automated, end-to-end check in `evals/scenarios.yaml` (`python -m evals.run`), run offline
through the real pipeline: guardian, fact extraction, intent, questions, calculators, official data, validator.
The table shows which pillar and intent handle it, what the assistant asks for before answering, and what
deterministic tools and official data shape the answer.

## Grow your know-how (`knowledge`)

| # | Person says | Intent | Asks for (in order) | Tools / data that shape the answer |
|---|---|---|---|---|
| S01 | I have AED 250,000 saved. I'm 58 and want to retire at 60. Can I afford to? | `retirement_readiness` | monthly_spending, monthly_income | savings_runway |
| X05 | I have AED 50,000 saved and want to retire. I spend 5,000 a month | `retirement_readiness` | monthly_income, monthly_income | savings_runway |
| S05 | My expenses are AED 6,000 a month. How much should I have before I stop working? | `how_much_needed` | monthly_income, current_age | emergency_fund, retirement_capital_needed |
| S25 | I'm worried about running out of money if I live to 90 or 95. | `longevity` | savings, monthly_spending, monthly_income, current_age | longevity_stress |
| S02 | Prices keep going up. Is my AED 100,000 still worth the same? | `purchasing_power` | nothing (enough was said) | inflation, inflation_purchasing_power |
| S04 | If I keep AED 200,000 in cash, will inflation eat my savings? | `purchasing_power` | nothing (enough was said) | inflation, deposit_vs_inflation |
| S17 | The news says inflation is falling. Does that mean my living costs will go down? | `inflation_news` | nothing (enough was said) | inflation |
| S03 | I receive AED 4,000 a month. Can I live on that when I retire? | `income_sufficiency` | monthly_spending | monthly_budget, future_cost |
| S10 | This fund returned 12% last year. Is that better than leaving my money in the bank? | `fund_vs_bank` | amount | investment_loss |
| S11 | My investment says I earn 8%, but my money doesn't seem to grow much. | `return_gap` | invested, years, actual_balance | return_reconcile |
| S09 | I have AED 80,000 in one company's shares. They've done well. Should I buy more? | `concentration` | total_investable | concentration |
| S08 | Everyone is talking about gold. Should I put my retirement money into it? | `asset_hype` | amount, total_investable | concentration, investment_loss |
| - | (What does this word mean?) | `explain_term` | - | glossary (tests/test_conversation_api.py) |

## Decide with care (`decisions`)

| # | Person says | Intent | Asks for (in order) | Tools / data that shape the answer |
|---|---|---|---|---|
| S06 | My bank offers 4% interest on a fixed deposit. Should I accept? | `deposit_offer` | amount | inflation, deposit_vs_inflation |
| S07 | The stock market has fallen. Should I sell everything before I lose more? | `market_drop` | amount, loss_pct, monthly_spending | investment_loss |
| S18 | There's worrying news about the global economy. Will my retirement savings be affected? My portfolio has falle... | `market_drop` | amount, monthly_spending | investment_loss |
| S12 | I owe AED 30,000, but I also have savings. Should I use my savings to clear the debt? | `debt_vs_savings` | debt_rate_pct, savings, monthly_spending | debt_vs_savings |
| S13 | My bank is offering me a loan of 100,000. The monthly payment looks affordable. | `loan_offer` | debt_rate_pct, years, monthly_income, monthly_spending | loan_cost, income_stress |
| S14 | My bank statement has charges I don't recognise. They're small, so should I ignore them? | `unknown_charges` | charges | charges_total |
| S15 | My insurance premium has gone up. Should I cancel my policy? | `insurance_premium` | old_premium, new_premium | premium_change |
| S16 | I heard interest rates are changing. Does that affect my savings? | `rate_change` | amount, deposit_rate_pct | rate_change |
| S19 | The UAE property market is booming. Should I sell my home and invest the money? | `sell_home` | sale_price, mortgage_balance, annual_rent | property_sale |
| S20 | My family sends money overseas every month. Is now a good time to transfer it? | `remittance` | remit_amount, exchange_rate, transfer_fee | remittance_compare |
| S21 | My medical costs might rise as I get older. How do I prepare? | `medical_costs` | savings, monthly_spending, monthly_medical | savings_runway |
| S22 | My adult children depend on me financially. Can I still retire? | `family_support` | savings, monthly_spending, monthly_support, monthly_income | family_support |
| S23 | I'm 62 and just lost my job. How do I make my savings last? | `job_loss` | savings, monthly_spending, monthly_income | savings_runway |
| S24 | I live in the UAE, but I may move back to my home country when I retire. What should I consider? | `relocation` | savings, monthly_spending | savings_runway |
| S26 | This company is on the UAE regulator's website. Does that mean my money is protected? | `deposit_protection` | nothing (enough was said) | explanation + official referral |
| S28 | My bank says my deposit is safe. What happens if the bank fails? | `deposit_protection` | nothing (enough was said) | explanation + official referral |
| S29 | A friend says there's a new government benefit or pension programme for people my age. Can I get it? | `benefit_programme` | nothing (enough was said) | explanation + official referral |

## Avoid scams (`scams`)

| # | Person says | Intent | Asks for (in order) | Tools / data that shape the answer |
|---|---|---|---|---|
| S30 | I have AED 180,000 saved, spend AED 5,500 a month, and someone is offering me a 'safe' investment paying 10% a... | `suspicious_offer` | nothing (enough was said) | savings_runway, promised_return |
| S27 | I saw a warning about an investment company. Is it the same one offering me a product? It's called Faraz Finan... | `check_entity` | nothing (enough was said) | check_entity |
| X01 | Is 4T Global Markets Financial Services licensed? Their site is 4t-global-invest.com | `check_entity` | nothing (enough was said) | check_entity |
| X02 | Is a company called Zentrova Wealth Partners licensed? | `check_entity` | nothing (enough was said) | check_entity |
| - | (Is this influencer allowed to promote investments?) | `finfluencer_check` | entity_name | tested in tests/test_conversation_api.py |
| - | (How do I spot investment scams?) | `scam_tips` | - | tested in tests/test_conversation_api.py |

## Protection cases beyond the 30

| # | Person says | Expected |
|---|---|---|
| X01 | Is 4T Global Markets Financial Services licensed? Their site is 4t-global-invest.com | check_entity: Licensed firm, different website |
| X02 | Is a company called Zentrova Wealth Partners licensed? | check_entity: Name not on any list |
| X03 | Someone from the bank called and asked for the OTP they sent me | STOP: Active scam - code requested |
| X04 | I already sent them 20,000 dirhams yesterday | SENT: Already sent money |
| X05 | I have AED 50,000 saved and want to retire. I spend 5,000 a month | retirement_readiness: Misheard amount corrected |

## Rules every answer follows

* Missing facts are asked for one at a time, most important first; nothing is assumed to be zero unless the person says so.
* Numbers come only from `tools/calculators.py`, the official datasets, or the person's own words (validator rule R3).
* Official inflation (FCSC CPI) is used when relevant, with its period and source, so new data changes the result.
* No product recommendations, no guarantees, no "this company is safe" (validator rules R1, R2, R4).
* Entity checks always say what they cannot establish; a missing record is never reassurance (rule R5).
* If official data is unavailable or stale, the answer says so (rule R6) and labels any stand-in value as an assumption.
* Where data the assistant does not have would be needed (live market prices, exchange rates, bank rates, benefit rules),
  it says so plainly and points to the official source instead of inventing it.
