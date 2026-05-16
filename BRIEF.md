# Project Brief

**Project Name:** Dime Tax Tracker
**Date Started:** 2026-05-15
**Date Closed:**
**Owner:** User (Thai investor, US stock portfolio via Dime Application)
**Assigned Agents:** Josh (Research), Ice (Requirements), Dot (Build), Quinn (QA), Serena (Delivery)

---

## Objective

Build a personal web application that tracks US stock investments made through the Dime Application and computes Thai capital gains tax liability in THB for annual PND 90 filing with the Revenue Department.

## Background

The user is a Thai tax resident who trades US stocks through the Dime Application (powered by Alpaca Broker). Under Thai Revenue Department rules effective 1 January 2024, foreign income is assessable when remitted to Thailand — not when realised. The user needs a tool that:
1. Tracks all investment activity (buys, sells, dividends)
2. Separates pre-2024 (grandfathered) from post-2024 (assessable) lots
3. Computes capital gains in THB at the correct BOT exchange rate per transaction
4. Tracks remittance events as the actual tax trigger
5. Produces a PND 90 summary at year-end

No automated data pipeline from Dime/Alpaca is confirmed — historical data onboarding is a known hard problem requiring a manual/CSV fallback solution.

## Scope

**Included:**
- Transaction logging (buy, sell, dividend, fee)
- Lot tracking with FIFO cost basis (pending resolution of OQ-07)
- Remittance log as the tax event
- BOT exchange rate integration (or manual override)
- Capital gains computation (THB) with pre/post-2024 classification
- Dividend tracking with US withholding tax for foreign tax credit
- PND 90 summary report
- Historical data onboarding (manual entry + CSV upload path)
- Single-user web app deployed on Railway, source on GitHub

**Excluded:**
- Multi-user / shared access
- Thai income from sources other than US stock investments
- Automated broker API data sync (Dime/Alpaca)
- Tax filing submission (app produces the data; filing is manual)
- Thai salary or other domestic income tracking
- Cryptocurrency
- Options, futures, or derivatives

## Success Criteria

1. User can log all historical trades manually without data loss risk
2. For any remittance event, the app correctly computes assessable income (post-2024 lots only)
3. PND 90 summary is accurate: correct THB capital gains figure, correct foreign tax credit from US withholding
4. App deployed on Railway, accessible from any browser, no local setup required
5. Quinn signs off on all tax computation scenarios before release

## Key Constraints

- No confirmed export from Dime — manual entry must be viable
- Thai tax law has an open question on cost basis method (FIFO vs average) — implementation must be configurable or default to the safer choice
- 2025 draft legislation potentially changes assessability rules — design must be able to accommodate a rule change without a full rewrite
- Single-user: no team features, no shared data, no multi-account management
- BOT exchange rate: must be applied per-transaction (not a single monthly rate)

## Stakeholders

- User: sole end user and product owner
- Dot: implementing engineer
- Quinn: QA and release sign-off
- Ice: requirements author
- Josh: research input (Thai tax rules, Dime/Alpaca data)
