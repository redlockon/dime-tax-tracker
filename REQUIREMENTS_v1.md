REQUIREMENTS DOCUMENT — Dime Tax Tracker
Version: v1 | Date: 2026-05-15
Input source: Josh Research Brief (Thai tax rules, Dime/Alpaca data context) + User requirements via Serena

---

## CONTEXT

Dime Tax Tracker is a personal web application for a Thai tax-resident investor who trades US stocks through the Dime Application. The app tracks all investment transactions, computes capital gains in THB using BOT exchange rates, logs remittance events (the actual Thai tax trigger), and produces an annual PND 90 summary. It is deployed on Railway, source on GitHub, single-user only.

The defining constraint: Thai tax liability on foreign income is triggered by **remittance** to Thailand, not by the realisation of the gain. A gain realised in October and remitted in February is taxable in February's tax year. Income earned before 1 January 2024 is grandfathered — not assessable regardless of when it is remitted. The app must model this correctly.

---

## MODULE 1 — PORTFOLIO & TRANSACTION LOGGING

### Context

The core data store. Every buy, sell, dividend, and fee event is recorded here. Lot tracking is essential for cost basis computation. Each transaction must carry enough metadata for the tax engine to classify it correctly (pre/post-2024, currency, exchange rate at transaction date).

---

### USER STORIES

**US-1.1:** As the user, I want to log a buy transaction with date, symbol, quantity, price per share, total cost, and currency, so that I have an accurate cost basis for each lot.

Acceptance Criteria:
- Given I submit a buy form with all required fields, when I save, then a new lot is created with: symbol, acquired date, quantity, price per share (USD), total cost (USD), BOT rate at acquired date (auto-fetched or manually entered), total cost (THB), and a pre/post-2024 flag computed from the acquired date.
- Given the acquired date is before 2024-01-01, when the lot is saved, then it is tagged `GRANDFATHERED = true`.
- Given the acquired date is 2024-01-01 or later, when the lot is saved, then it is tagged `GRANDFATHERED = false`.
- Given the BOT rate API is unavailable, when I submit a buy, then the system prompts me to enter the rate manually and does not block the save.
- Given I submit a buy with a missing required field, when I attempt to save, then a field-level validation error is shown and the form is not submitted.

**US-1.2:** As the user, I want to log a sell transaction that is matched against existing lots, so that cost basis and realised gain/loss are computed automatically.

Acceptance Criteria:
- Given I submit a sell with symbol, date, quantity, proceeds (USD), and BOT rate, when I save, then the system applies the configured lot-matching method (default: FIFO) and records: lots consumed, cost basis (USD), cost basis (THB), proceeds (USD), proceeds (THB), realised gain/loss (USD and THB), and disposal date.
- Given a sell partially depletes a lot, when saved, then the lot is split: the disposed portion is closed, the remainder stays open with correct quantity and original cost per share.
- Given a sell quantity exceeds available open lots, when I attempt to save, then the system blocks the save with an error: "Insufficient shares: [X] shares available, [Y] requested."
- Given the sell date is before the acquired date of any matched lot, when I attempt to save, then the system blocks with an error: "Disposal date precedes acquisition date."

**US-1.3:** As the user, I want to log dividend payments with the USD amount and US tax withheld, so that I can compute the foreign tax credit on PND 90.

Acceptance Criteria:
- Given I log a dividend with symbol, payment date, gross amount (USD), US tax withheld (USD, default 15%), and BOT rate, when saved, then the system records gross dividend (THB), US tax withheld (THB), and net dividend (THB).
- Given I enter a US withholding rate other than 15%, the system accepts it and uses that rate without overriding.
- Given the W-8BEN status is unknown for the symbol, the system does not auto-set the withholding rate — the user must enter it manually.

**US-1.4:** As the user, I want to log fees (brokerage, foreign exchange, wire) so that I have a complete cost record.

Acceptance Criteria:
- Given I log a fee with date, type (brokerage / FX / wire / other), amount (USD or THB), and optional note, when saved, then it appears in the transaction list and is available for the tax engine to deduct against proceeds (when applicable — see OQ-06).

**US-1.5:** As the user, I want to view, edit, and delete any logged transaction, so that I can correct data entry errors.

Acceptance Criteria:
- Given I view the transaction list, all transactions appear sorted by date descending, with symbol, type, date, amount (USD), amount (THB), and lot impact visible.
- Given I edit a transaction, when I save changes, all downstream computations (lot balances, tax figures) are recalculated.
- Given I delete a transaction, the system warns: "Deleting this transaction will affect [N] dependent lots and tax calculations. Confirm?" and requires explicit confirmation.
- Given a transaction is referenced by a remittance event, deletion is blocked with: "This transaction is linked to a remittance record. Remove the remittance link first."

---

### FUNCTIONAL REQUIREMENTS

**FR-1.01:** The system shall support four transaction types: BUY, SELL, DIVIDEND, FEE.

**FR-1.02:** Every transaction shall store: transaction_id (UUID), type, symbol (US ticker), date (YYYY-MM-DD), quantity (for BUY/SELL; null for DIVIDEND/FEE), unit_price_usd, total_usd, bot_rate_thb_per_usd, total_thb, notes (free text), created_at, updated_at.

**FR-1.03:** Every BUY transaction shall create one Lot record containing: lot_id, symbol, acquired_date, original_quantity, remaining_quantity, cost_per_share_usd, total_cost_usd, bot_rate_at_acquisition, total_cost_thb, grandfathered (boolean), source (MANUAL / CSV / IMPORT).

**FR-1.04:** Lot matching on SELL shall use FIFO by default. The method shall be a configurable application setting (see OQ-07).

**FR-1.05:** Every SELL shall produce a Disposal record containing: disposal_id, sell_transaction_id, lots_consumed (array of lot_id + quantity consumed), total_cost_basis_usd, total_cost_basis_thb, proceeds_usd, proceeds_thb, realised_gain_usd, realised_gain_thb, disposal_date, grandfathered_portion_thb, assessable_portion_thb.

**FR-1.06:** A disposal that spans both grandfathered and post-2024 lots shall split gain proportionally: `assessable_gain = total_gain × (assessable_cost_basis / total_cost_basis)`. (See EC-1.3 for the mixed-lot scenario.)

**FR-1.07:** The BOT reference rate shall be fetched automatically for each transaction date. The fetch endpoint is the Bank of Thailand's public API (see OQ-01). If unavailable, the system shall fall back to manual entry without blocking the save.

**FR-1.08:** All monetary amounts shall be stored with 6 decimal places of precision. Display rounding (2 decimal places) is separate from stored precision.

**FR-1.09:** Exchange rate used for each transaction shall be stored immutably at the time of entry. Subsequent BOT rate updates shall not retroactively change historical records.

**FR-1.10:** The system shall maintain a running position per symbol: total shares held, total cost basis (USD and THB), unrealised gain/loss using last known price (display only, not for tax purposes).

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-1.01:** All transaction writes shall be atomic — a buy that creates a lot must not leave orphaned transactions if the lot creation fails.

**NFR-1.02:** The transaction log shall support a minimum of 5,000 records without UI degradation (pagination or virtual scrolling required beyond 100 rows).

**NFR-1.03:** Transaction forms shall validate all fields client-side before submission. Server-side validation is the source of truth.

**NFR-1.04:** Lot and disposal records shall never be soft-deleted — mark as VOIDED with a reason if needed. Full history is preserved.

---

### EDGE CASES & EXCEPTIONS

**EC-1.1:** Selling shares bought on the same day (day-trade). FIFO still applies; the lot created earlier in the same day is consumed first. If two lots have the same date, they are sorted by created_at timestamp.

**EC-1.2:** Stock split or reverse split. The system must allow a corporate action entry type that adjusts lot quantities and cost-per-share without triggering a taxable event. (See OQ-05 — corporate actions are a scope boundary question.)

**EC-1.3:** Mixed-lot disposal — a sell consuming both pre-2024 (grandfathered) and post-2024 lots. The system must compute two sub-gains: grandfathered (not assessable) and post-2024 (assessable). The tax engine must never aggregate these into a single figure.

**EC-1.4:** Selling at a loss. The system records the negative gain correctly. Losses do not offset gains in Thai PIT (no loss carry-forward under current rules — see OQ-08). The tax engine must not net a loss against another lot's gain.

**EC-1.5:** BOT rate unavailable for a weekend or Thai holiday date. The system shall offer the most recently available rate as a default, clearly labelled "Rate from [date] — transaction date rate unavailable." User must confirm before saving.

**EC-1.6:** Dividend reinvestment (DRIP). Treated as: dividend payment (logged) + buy transaction (logged) at the reinvestment price. Not a single compound transaction.

---

### OPEN QUESTIONS

**OQ-01:** Which BOT API endpoint provides daily reference rates, and is it publicly accessible without authentication?
- Owner: Dot (technical feasibility check)
- Blocker: FR-1.07 implementation

**OQ-02:** Does the user need multi-currency dividend support (non-USD dividends from ADRs)?
- Owner: User
- Impact: FR-1.02 currency field scope

---

## MODULE 2 — REMITTANCE LOG

### Context

Remittance to Thailand — not realisation of gain — is the Thai tax trigger for foreign income. This module is a separate ledger that records every transfer of funds from the user's US brokerage/bank account into Thailand. The tax engine uses remittance records, not disposal records, to determine which tax year income becomes assessable.

---

### USER STORIES

**US-2.1:** As the user, I want to log each remittance to Thailand with the amount, date, and source currency, so that I have a precise record of when and how much foreign income I brought into Thailand.

Acceptance Criteria:
- Given I submit a remittance with date, amount (USD), BOT rate, THB received, remittance type (wire / PromptPay / other), and optional note, when saved, then the record is stored and the running total of unremitted post-2024 gains is updated.
- Given the THB received field differs from (USD amount × BOT rate) by more than 2%, the system flags a warning: "THB received differs from expected by [X]%. Check for bank fees or FX spread. Confirm?" — but does not block the save.

**US-2.2:** As the user, I want to see my cumulative unremitted post-2024 capital gains balance, so that I know the maximum assessable amount if I remit funds today.

Acceptance Criteria:
- Given I view the Remittance dashboard, I see: total realised post-2024 gains (THB), total previously remitted and assessed (THB), and current unremitted balance (THB).
- Given I have made no remittances, the unremitted balance equals total realised post-2024 gains.
- Given grandfathered gains are included in a disposal record, they are excluded from the unremitted balance calculation.

**US-2.3:** As the user, I want to link a remittance event to specific disposal events it covers, so that the tax engine knows which gains are assessable in which tax year.

Acceptance Criteria:
- Given I view an unlinked remittance record, I can select which disposals (or portion thereof) that remittance covers.
- Given a remittance amount is smaller than a single disposal gain, the system allows partial linking with an allocated amount.
- Given a disposal has been fully allocated across previous remittances, it no longer appears in the unlinked pool.
- Given I attempt to allocate more gain to a remittance than the remittance amount, the system blocks with an error.

---

### FUNCTIONAL REQUIREMENTS

**FR-2.01:** The Remittance table shall store: remittance_id, date, amount_usd, bot_rate, amount_thb, remittance_type, linked_disposals (array of {disposal_id, allocated_thb}), tax_year (computed from remittance date), notes.

**FR-2.02:** Tax year shall be computed as the calendar year of the remittance date (Thai tax year = calendar year). A remittance on 2025-02-15 belongs to tax year 2025.

**FR-2.03:** The system shall maintain an "unremitted post-2024 gains pool" — a running balance of all assessable realised gains that have not yet been linked to a remittance. This balance shall update automatically when new disposals are recorded or new remittances are linked.

**FR-2.04:** Grandfathered gains (pre-2024) shall never appear in the unremitted pool or any remittance allocation — they are informational only.

**FR-2.05:** The system shall not require remittance records to be linked to specific disposals — a simple total-amount remittance log is valid. Disposal linking is an optional enhancement for users who want precise traceability (see OQ-03).

**FR-2.06:** The system shall allow a remittance to be marked as "principal return" (returning original investment capital, not gains). Principal returns are not assessable. The user must manually designate this — the system does not infer it.

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-2.01:** Remittance records shall be immutable once created — no editing, only voiding with a reason. This mirrors how financial institutions treat wire transfer records.

**NFR-2.02:** The unremitted gains balance shall be computed in real time on page load — no caching of this figure.

---

### EDGE CASES & EXCEPTIONS

**EC-2.1:** Remittance in the same tax year as realisation. Valid — a stock sold in March and remitted in October is assessable in that same tax year. The system handles this without special logic.

**EC-2.2:** Remittance that exceeds total assessable realised gains. Could happen if the user remits a mix of gains + original principal. The system shall warn: "Remittance amount exceeds recorded assessable gains. Difference may be principal return. Please review allocation." Does not block the save.

**EC-2.3:** Partial-year entry (app starts mid-year, prior remittances exist). The historical onboarding flow (Module 4) must handle prior remittances. The user must be able to enter remittance records with backdated dates — the system does not restrict remittance dates to the current year.

**EC-2.4:** Remittance before any corresponding disposal. Valid use case — user sends accumulated cash from prior sales. System links to whatever pool of assessable gains exists at that time.

**EC-2.5:** Multiple remittances in one tax year. Handled natively — the system groups by tax year. PND 90 reporting shows the total for each tax year, not per-remittance.

---

### OPEN QUESTIONS

**OQ-03:** Does the user want disposal-level linking for remittances, or is total-amount per tax year sufficient for PND 90?
- Owner: User
- Impact: FR-2.05 scope, UI complexity of remittance form

---

## MODULE 3 — TAX COMPUTATION ENGINE

### Context

The engine that translates raw transaction and remittance data into Thai tax liability. Two income streams: capital gains (treated as ordinary income under Thai PIT) and dividends (eligible for foreign tax credit from US withholding). Output is the PND 90 annual summary. The engine must be transparent — every figure must be traceable to its source transactions.

---

### USER STORIES

**US-3.1:** As the user, I want to see my computed Thai tax liability for any given tax year based on my remittances, so that I know what to declare on PND 90.

Acceptance Criteria:
- Given I select a tax year, the engine displays: total assessable capital gains remitted (THB), total assessable dividends received (THB), combined assessable foreign income (THB), estimated PIT at progressive rates (THB), foreign tax credit from US withholding (THB), estimated net Thai tax payable (THB).
- Given I have no remittances in a selected tax year, all figures show 0 and the display reads "No assessable income for [year]."
- Given I have grandfathered gains in the same year, they are shown in a separate non-assessable section — clearly labelled — and do not affect the assessable total.

**US-3.2:** As the user, I want to enter my other income (salary, business, rental) so that the tax engine can compute my actual marginal rate and total PIT liability.

Acceptance Criteria:
- Given I enter other income for a tax year, the engine recalculates total taxable income and recomputes progressive rate brackets accordingly.
- Given I enter no other income, the engine calculates tax on investment income only and labels the result "Investment income only — add other income for total liability."
- Given total income falls below the 150,000 THB personal exemption, net tax payable shows 0.

**US-3.3:** As the user, I want a PND 90 line-item summary I can copy directly into the form, so that I minimise the risk of transcription errors during filing.

Acceptance Criteria:
- Given I generate the PND 90 summary for a tax year, the output shows each line mapped to its PND 90 form field with the computed value.
- Given I print or export the summary, it includes a header: "Tax year [YYYY] — Generated [date] — Exchange rate method: [BOT / Commercial bank]" and a disclaimer: "This is a computation aid. Verify figures with a licensed Thai tax advisor before filing."
- Given the PND 90 field mapping contains open questions (OQ-04), the affected fields are marked [PENDING — see OQ-04] in the output.

**US-3.4:** As the user, I want to see the foreign tax credit calculation clearly separated, so that I can correctly claim the US withholding offset.

Acceptance Criteria:
- Given dividends with US withholding are recorded, the engine shows: gross dividend (THB), US tax withheld (THB), Thai PIT on dividend income (THB), claimable foreign tax credit = min(US tax withheld, Thai PIT on that income), net additional Thai tax on dividends (THB).
- Given the US withholding exceeds the Thai PIT on dividend income, the excess is shown as non-claimable (Thai law does not allow refund of excess foreign tax credit).

---

### FUNCTIONAL REQUIREMENTS

**FR-3.01:** The engine shall operate on a tax-year basis (calendar year). All inputs are filtered by tax year before computation.

**FR-3.02:** Assessable capital gains for a tax year = sum of remittance-allocated assessable disposal gains whose remittance date falls in that calendar year.

**FR-3.03:** Assessable dividend income for a tax year = sum of dividends received (gross, THB) whose payment date falls in that tax year. (Note: dividends are not subject to the remittance rule in the same way as capital gains — see OQ-09 for clarification.)

**FR-3.04:** Thai PIT shall be computed using the progressive bracket structure:
- 0 – 150,000 THB: 0%
- 150,001 – 300,000 THB: 5%
- 300,001 – 500,000 THB: 10%
- 500,001 – 750,000 THB: 15%
- 750,001 – 1,000,000 THB: 20%
- 1,000,001 – 2,000,000 THB: 25%
- 2,000,001 – 5,000,000 THB: 30%
- 5,000,001+ THB: 35%

**FR-3.05:** The PIT computation shall accept an "other income" input (THB) per tax year. Total income = other income + assessable foreign income. Tax is computed on total income. Tax attributable to foreign income = total tax − tax on other income alone.

**FR-3.06:** Foreign tax credit = min(US withholding withheld in THB, Thai PIT computed on dividend income in that year). Excess is non-claimable and shown informatively.

**FR-3.07:** Net Thai tax payable = Thai PIT on total income − foreign tax credit − standard deductions (see OQ-10 for deduction scope).

**FR-3.08:** All computation steps shall be stored as an audit trail per tax year: inputs, intermediate values, output. The user shall be able to view the full computation breakdown.

**FR-3.09:** The exchange rate method (BOT reference rate vs commercial bank rate) shall be configurable at the application level and applied consistently to all transactions. The current setting shall be displayed on every tax report.

**FR-3.10:** Bracket computation shall be explicit and displayed per bracket — not just a total figure. Example: "300,001–500,000: 200,000 THB × 10% = 20,000 THB."

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-3.01:** Tax computations shall be deterministic — the same inputs must always produce the same outputs. No floating-point rounding ambiguity; all monetary arithmetic shall use integer arithmetic in satang (1/100 THB).

**NFR-3.02:** Tax bracket rates and thresholds shall be stored as configuration, not hardcoded — so that a legislative change can be updated in one place.

**NFR-3.03:** The PND 90 summary shall be printable (print-friendly CSS) and exportable as PDF (via browser print-to-PDF). No server-side PDF generation required.

**NFR-3.04:** The engine shall compute a complete tax year summary in under 2 seconds for a portfolio with up to 5,000 transactions.

---

### EDGE CASES & EXCEPTIONS

**EC-3.1:** Tax year spanning the 2024 rule-change boundary. E.g., computing 2024 tax year: only gains from lots acquired post-2024-01-01 and remitted in 2024 are assessable. Lots acquired in 2023 and sold and remitted in 2024 — the gains are assessable (the gain is post-2024, even if the lot was pre-2024). The grandfathered flag is on the LOT (acquired before 2024), not on the disposal event. This is the single most important correctness requirement in the system.

**EC-3.2:** Negative assessable income (capital loss). Thai PIT has no loss carry-forward for investment income. If assessable disposals show a net loss, assessable income for that category = 0. The loss is shown informatively but not used to offset other income.

**EC-3.3:** Remittance in a different tax year than gain realisation. A gain realised in 2024 but remitted in 2026 is assessed in 2026, not 2024. The engine must use the remittance date's tax year, not the disposal date's tax year.

**EC-3.4:** Draft 2025 legislation that may exempt some foreign income categories. The tax bracket config structure must allow for an "exempted income" input field per tax year, so that if new exemptions pass, the user can enter the exempt amount without a code change.

**EC-3.5:** User has both W-8BEN (15% withholding) and non-W-8BEN holdings. The system uses the per-dividend withholding amount as entered — it does not assume a universal rate. The foreign tax credit is the sum of actual withholding, not 15% of gross dividends.

---

### OPEN QUESTIONS

**OQ-04:** What are the exact PND 90 form field labels and line numbers for: (a) foreign capital gains income, (b) foreign dividend income, (c) foreign tax credit claim?
- Owner: Josh (research, plus User to verify against their most recent PND 90 form)
- Blocker: US-3.3 PND 90 field mapping

**OQ-05:** Are corporate actions (stock splits, mergers, spin-offs) in scope for v1?
- Owner: User
- Impact: EC-1.2 — if yes, adds significant complexity to lot adjustment logic

**OQ-06:** Are brokerage fees deductible from capital gains for Thai tax purposes?
- Owner: Josh (Thai Revenue Department guidance on allowable deductions)
- Impact: FR-1.04, FR-3.07 — if yes, fee transactions must be linked to disposals

**OQ-07:** What cost basis method does the Thai Revenue Department accept for US stock capital gains — FIFO, LIFO, or average cost?
- Owner: Josh (flagged as unresolved in Research Brief)
- Blocker: FR-1.04 — if average cost, lot-level tracking still needed but matching logic changes

**OQ-08:** Does Thai PIT allow capital loss carry-forward against future capital gains?
- Owner: Josh (flagged as requiring Revenue Department confirmation)
- Impact: EC-3.2 — if yes, the engine needs a carry-forward ledger per tax year

**OQ-09:** Are dividends subject to the same remittance-based assessability rule as capital gains, or are they assessable when received regardless of remittance?
- Owner: Josh (flagged as gap — needs Revenue Department clarification)
- Blocker: FR-3.03 — current implementation assumes dividends are assessed when received, not when remitted. If wrong, dividends need a remittance log too.

**OQ-10:** What standard personal deductions should the app pre-populate (personal allowance, spouse, children, etc.)? Should the app compute after deductions or show gross tax only?
- Owner: User
- Impact: FR-3.07 scope — deductions significantly affect final payable figure

---

## MODULE 4 — HISTORICAL DATA ONBOARDING

### Context

The hard problem. The user has been trading since before 2024. No confirmed automated export from Dime/Alpaca exists. Historical lots must be entered for cost basis continuity — a 2022 purchase that is sold in 2025 still uses the 2022 cost basis to compute the gain, even though the gain is assessable. This module defines exactly how historical data gets into the system safely and verifiably.

---

### USER STORIES

**US-4.1:** As the user, I want to manually enter historical transactions one at a time, so that I can reconstruct my portfolio even without an export file.

Acceptance Criteria:
- Given I open the manual entry form with historical mode enabled, I can enter any date (including dates before the app existed).
- Given I enter a historical buy with a date before 2024-01-01, the lot is automatically tagged GRANDFATHERED = true.
- Given I save a historical buy, it appears in the lot ledger with source = MANUAL and is immediately available for lot matching on future sells.
- Given I enter a historical sell, the system matches lots chronologically (FIFO) against all available lots — including manually entered historical ones — and produces a disposal record.

**US-4.2:** As the user, I want to upload a CSV file of historical transactions, so that I can onboard bulk data without entering each trade individually.

Acceptance Criteria:
- Given I upload a CSV, the system validates the file against the required schema (see FR-4.03) and shows a preview of parsed rows before import.
- Given the CSV contains rows with missing or invalid fields, the system flags each problematic row with the specific error (e.g., "Row 12: date format unrecognised") and allows me to download a corrected-rows-only CSV for re-upload. Valid rows can be imported independently.
- Given I confirm the import, all parsed transactions are created with source = CSV_IMPORT and tagged accordingly.
- Given the BOT rate is not in the CSV, the system auto-fetches the rate for each transaction date. If unavailable for a specific date, the system flags that row for manual rate entry before import is finalised.

**US-4.3:** As the user, I want to enter a "portfolio snapshot" as of a specific date instead of reconstructing every trade, so that I have a valid cost basis starting point even if I cannot recover all historical transaction history.

Acceptance Criteria:
- Given I enter a snapshot with: as-of date, symbol, quantity, average cost per share (USD), and I confirm I am unable to reconstruct full history, then the system creates synthetic lots with source = SNAPSHOT and a warning label: "Cost basis from snapshot — individual lot history unavailable."
- Given a snapshot lot is consumed by a future sell, the disposal record notes "Cost basis from snapshot — may be imprecise."
- Given a snapshot date is before 2024-01-01, the snapshot lots are tagged GRANDFATHERED = true.
- Given a snapshot date is 2024-01-01 or later, the system warns: "Post-2024 snapshot lots will be treated as assessable. Ensure cost basis is accurate."

**US-4.4:** As the user, I want a checklist-based onboarding flow that guides me through entering historical data in the correct order, so that I do not accidentally create data integrity issues.

Acceptance Criteria:
- Given I open the onboarding flow for the first time, I see the checklist: Step 1 — Choose method (manual / CSV / snapshot); Step 2 — Enter earliest trades first; Step 3 — Verify lot summary matches known portfolio; Step 4 — Enter historical remittances; Step 5 — Mark onboarding complete.
- Given I complete all steps and mark onboarding complete, the historical flag is removed from the dashboard and regular transaction entry mode activates.
- Given I have not completed onboarding, the dashboard shows a persistent banner: "Historical data onboarding incomplete — tax calculations may be inaccurate."

---

### FUNCTIONAL REQUIREMENTS

**FR-4.01:** The system shall accept transaction dates from 2010-01-01 onwards (10-year lookback minimum). No minimum date restriction beyond this.

**FR-4.02:** All manually entered or imported historical transactions shall be marked with a source field: MANUAL, CSV_IMPORT, or SNAPSHOT. This field is immutable after creation.

**FR-4.03:** The CSV import schema shall accept the following column formats:

| Column | Required | Format | Notes |
|--------|----------|--------|-------|
| type | Yes | BUY / SELL / DIVIDEND / FEE | Case-insensitive |
| symbol | Yes | US ticker | e.g. AAPL |
| date | Yes | YYYY-MM-DD | ISO 8601 only |
| quantity | Conditional | Decimal | Required for BUY / SELL |
| unit_price_usd | Conditional | Decimal | Required for BUY / SELL |
| total_usd | Yes | Decimal | Gross amount |
| bot_rate | No | Decimal | If blank, auto-fetched |
| notes | No | String | Free text |

**FR-4.04:** The system shall also attempt to parse the Alpaca export format (fields: Acquired Date, Disposed Date, Symbol, Cost, Proceed, NetAmount) and map it to the internal schema. Date accuracy issues from Alpaca exports (flagged in Research Brief) shall be surfaced row-by-row in the preview step with a warning: "Alpaca export date accuracy is unconfirmed — verify acquired dates before importing."

**FR-4.05:** The snapshot entry form shall compute a synthetic lot with: lot_id, symbol, snapshot_date as acquired_date, quantity, average_cost_per_share_usd, total_cost_usd = quantity × average_cost, source = SNAPSHOT.

**FR-4.06:** The system shall allow mixed-method onboarding: some symbols via full transaction history (MANUAL/CSV), others via snapshot. The dashboard must distinguish which symbols have full lot history vs snapshot-based lots.

**FR-4.07:** Historical remittances (transfers made before the app existed) must be enterable via the standard Remittance Log form with backdated dates. No separate historical remittance flow is required.

**FR-4.08:** An onboarding status flag (complete / incomplete) shall be stored per user session. If incomplete, all tax computation outputs shall carry a banner: "Caution: onboarding incomplete — figures may not reflect full portfolio history."

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-4.01:** CSV files up to 10MB (approximately 50,000 rows) shall be processed within 30 seconds.

**NFR-4.02:** The CSV parser shall not require the user to match column names exactly — it shall attempt fuzzy column header matching and show the user the detected mapping before import confirmation.

**NFR-4.03:** All imported records shall be reversible via a bulk undo: "Undo import [timestamp]" removes all records created in that import batch. This is the only case where bulk deletion is permitted.

---

### EDGE CASES & EXCEPTIONS

**EC-4.1:** User has traded the same symbol across multiple years with partial sells. Full FIFO lot history is required for correct matching. A snapshot entered mid-history (e.g., snapshot as of 2023 for a position opened in 2019) loses the pre-snapshot lot detail. The system must warn the user that a mid-position snapshot may produce incorrect FIFO matching for any remaining shares.

**EC-4.2:** CSV contains duplicate rows (same date, symbol, quantity, price). The system detects duplicates in the preview step and flags them: "Row [X] appears to be a duplicate of Row [Y]. Import both?" The user must explicitly confirm duplicate rows.

**EC-4.3:** User has pre-2024 lots that were fully sold before 2024. These are grandfathered and produce no assessable gain. They still need to be entered if any subsequent transaction (dividend, corporate action) references the symbol. If the position is fully closed pre-2024, the user can skip onboarding those lots with a note.

**EC-4.4:** Alpaca export has wrong acquired dates. The preview step surfaces Alpaca data row-by-row. The user must manually verify and correct dates in the preview before confirming import. The system does not trust Alpaca acquired dates automatically.

**EC-4.5:** User cannot recover historical data at all. The snapshot method (US-4.3) is the fallback. The system must make clear that snapshot-based tax calculations carry uncertainty, especially for mixed pre/post-2024 lots.

---

### OPEN QUESTIONS

**OQ-11:** Can the user obtain a per-trade CSV from Dime directly? The Research Brief could not confirm this (help pages returned 403). User should test this in-app and report back.
- Owner: User (to test)
- Impact: If yes, FR-4.04 Alpaca format parsing covers it. If no, manual entry or snapshot is the primary path.

**OQ-12:** Does the user have the Dime "Annual Foreign Summary" PDF for prior years? If yes, Josh should map its field structure to determine how much can be auto-parsed.
- Owner: User (to provide a sample)
- Impact: Could enable a third import path from the annual summary PDF

---

## MODULE 5 — DASHBOARD & REPORTING

### Context

The user-facing layer. Not a trading tool — this is a tax clarity tool. The dashboard surfaces what the user needs to know: what they owe, why, and what to write on the form. Design optimises for trust and traceability over visual richness.

---

### USER STORIES

**US-5.1:** As the user, I want a portfolio summary on the home screen showing all current holdings, so that I can confirm the app's records match my actual Dime portfolio.

Acceptance Criteria:
- Given I open the dashboard, I see a holdings table with: symbol, shares held, average cost basis (USD), current price (fetched or manually entered), unrealised gain/loss (USD and THB), and lot count.
- Given a symbol has both grandfathered and post-2024 lots, the holdings row shows a split: "X shares (grandfathered) / Y shares (assessable)."
- Given real-time price data is unavailable, the row shows "Price unavailable — last updated [date]" and the unrealised gain/loss columns are blank (not zero).

**US-5.2:** As the user, I want a tax year summary page that shows my total liability estimate and all supporting figures, so that I can prepare for PND 90 filing.

Acceptance Criteria:
- Given I select a tax year, the page shows all assessable income (capital gains + dividends), the tax bracket breakdown, the foreign tax credit, and net estimated payable — all with THB amounts.
- Given I click any line item, I see the source transactions that produced that figure.
- Given I click "View PND 90 mapping," I see each figure mapped to its form field with line number.

**US-5.3:** As the user, I want to export my transaction history as a CSV, so that I have a backup and can cross-reference with Dime records.

Acceptance Criteria:
- Given I trigger an export, I receive a CSV with all transactions in the standard schema (FR-4.03 format), sorted by date ascending.
- Given the export is complete, the file is named: `dime-tax-tracker-export-[YYYY-MM-DD].csv`.

**US-5.4:** As the user, I want a "reconcile" view that lists my current holdings in the app alongside a field to enter what Dime actually shows, so that I can quickly find discrepancies.

Acceptance Criteria:
- Given I open the reconcile view, I see each symbol with: app-computed shares held, a text field for "Dime shows," and a status column (Match / Mismatch / Not entered).
- Given I enter a Dime quantity that differs from the app's figure, the row turns red and shows the delta.
- Given all symbols match, the view shows a green "Portfolio reconciled" banner.

---

### FUNCTIONAL REQUIREMENTS

**FR-5.01:** The dashboard home screen shall show: portfolio summary table (US-5.1), unremitted post-2024 gains balance (from Module 2), onboarding status (if incomplete, show banner), and current tax year quick-stats (assessable income to date, estimated liability).

**FR-5.02:** Live price data for unrealised gain calculation shall use a public API (Yahoo Finance via backend, consistent with TideWatch architecture — see OQ-13). If unavailable, positions are shown without unrealised P&L.

**FR-5.03:** The tax year selector shall default to the current calendar year. All prior years with data shall be available in the dropdown.

**FR-5.04:** The PND 90 mapping view shall be a structured table: PND 90 field name | PND 90 line number | Computed value (THB) | Notes. This view shall be printable.

**FR-5.05:** Transaction history shall be filterable by: date range, symbol, transaction type. Default view: current tax year, all symbols, all types.

**FR-5.06:** All currency displays shall show both USD and THB values. THB is primary for tax purposes; USD is secondary context.

**FR-5.07:** The app shall include a "Disclaimer" footer on all tax-related pages: "This tool assists computation only. It does not constitute tax advice. Verify all figures with a licensed Thai tax advisor before filing."

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-5.01:** The dashboard shall load completely (including API data fetch) in under 3 seconds on a standard broadband connection.

**NFR-5.02:** The app shall be fully functional on Chrome and Safari (desktop). Mobile browser support is desirable but not required for v1.

**NFR-5.03:** All monetary figures shall be displayed with thousand-separator formatting (e.g., 1,234,567.89 THB).

**NFR-5.04:** Colour coding: unrealised gains in green, losses in red, grandfathered amounts in grey (neutral — not actionable), assessable amounts in amber.

---

### EDGE CASES & EXCEPTIONS

**EC-5.1:** All positions are fully closed (no open lots). Portfolio summary shows empty holdings table with message: "No open positions. View historical transactions below."

**EC-5.2:** Tax year summary before any remittances. Assessable income shows 0, with explanatory note: "No remittances recorded for [year]. Capital gains become assessable upon remittance to Thailand."

**EC-5.3:** Current year is 2024 (the first assessable year). The app must not show 2023 as an assessable year even if the user has remittances dated in 2023 — those would be assessed on pre-2024 income, which is grandfathered. The 2023 tax year summary should show 0 assessable income with a note: "Pre-2024 income is grandfathered and not assessable."

---

### OPEN QUESTIONS

**OQ-13:** Should the dashboard reuse the TideWatch Python backend (`tidewatch_api.py`) for live price data, or implement a separate price-fetch endpoint?
- Owner: Dot (architecture decision)
- Impact: reduces duplication if combined; increases coupling between two projects

---

## MODULE 6 — AUTH & DEPLOYMENT

### Context

Single-user personal app. No multi-user auth required. Deployed on Railway with source on GitHub. The primary auth concern is preventing accidental public exposure of sensitive financial data — not multi-tenant access control.

---

### USER STORIES

**US-6.1:** As the user, I want the app to require a password before displaying any data, so that my financial records are not exposed if someone else accesses the Railway URL.

Acceptance Criteria:
- Given I visit the app URL without a session, I am redirected to a login page.
- Given I enter the correct password, I am granted a session and redirected to the dashboard.
- Given I enter an incorrect password, I am shown "Incorrect password" and the attempt is logged with timestamp and IP.
- Given my session has been active for more than 24 hours, I am required to re-authenticate.
- Given I click "Logout," my session is invalidated immediately and I am redirected to the login page.

**US-6.2:** As the user, I want the app deployed on Railway so that I can access it from any browser without running local software.

Acceptance Criteria:
- Given the app is deployed on Railway, it is accessible at the Railway-provided URL (or custom domain if configured).
- Given the Railway deployment receives a new push to the GitHub main branch, it rebuilds and redeploys automatically.
- Given the Railway deployment is healthy, the app responds within 5 seconds of a cold start.

**US-6.3:** As the user, I want all application data stored persistently, so that data is not lost between Railway deployments or container restarts.

Acceptance Criteria:
- Given I deploy a new version to Railway, all previously entered transactions and remittances are still present after deployment.
- Given the Railway container restarts, data is not lost.

---

### FUNCTIONAL REQUIREMENTS

**FR-6.01:** Authentication shall use a single hardcoded password stored as an environment variable (`APP_PASSWORD`) on Railway. No user accounts, no registration flow.

**FR-6.02:** Session management shall use a server-side session with a signed cookie. Session duration: 24 hours. The session secret shall be stored as an environment variable (`SESSION_SECRET`).

**FR-6.03:** Failed login attempts shall be rate-limited: maximum 5 attempts per IP per 15 minutes. After 5 failures, the IP is blocked for 15 minutes with a message: "Too many attempts. Try again in 15 minutes."

**FR-6.04:** All HTTP traffic shall be served over HTTPS. Railway provides HTTPS termination automatically — the app shall not serve on plain HTTP in production.

**FR-6.05:** Data persistence shall use a lightweight embedded database (SQLite with a persistent volume on Railway — see OQ-14) or a Railway-provisioned PostgreSQL instance. The choice must survive container restarts and redeployments.

**FR-6.06:** The GitHub repository shall contain: application source code, `requirements.txt` (Python) or `package.json` (Node), `Procfile` or Railway-compatible startup config, `.gitignore` excluding all `.env` files and the SQLite database file (if used), and a `railway.toml` if needed.

**FR-6.07:** The `APP_PASSWORD` and `SESSION_SECRET` shall never appear in the GitHub repository. Railway environment variables are the only storage location.

**FR-6.08:** The app shall expose a `/health` endpoint that returns HTTP 200 with `{"status": "ok"}`. Railway uses this for deployment health checks.

---

### NON-FUNCTIONAL REQUIREMENTS

**NFR-6.01:** The login page shall have no financial data visible — not even anonymised figures — before authentication.

**NFR-6.02:** All API endpoints shall return 401 Unauthorized if the request does not carry a valid session. There shall be no unauthenticated data endpoints.

**NFR-6.03:** The app shall start cleanly from zero state (empty database) on first deployment — no manual database initialisation step required.

**NFR-6.04:** Railway deployment shall use environment detection: `PORT` from Railway environment variable, `DEBUG=false` in production.

**NFR-6.05:** Database backups are out of scope for v1 — the user is responsible for periodic export via the CSV export feature (US-5.3). A future v2 should add automated backup.

---

### EDGE CASES & EXCEPTIONS

**EC-6.1:** Railway container restart causes SQLite file loss (if stored in ephemeral storage). This is a data loss risk. The system must use a Railway persistent volume or PostgreSQL — ephemeral SQLite is not acceptable. (See OQ-14.)

**EC-6.2:** User changes the `APP_PASSWORD` environment variable. All existing sessions should be invalidated. Implementation: the session validation step checks the password hash stored in the session against the current env var. If changed, existing sessions fail validation and redirect to login.

**EC-6.3:** First deployment with empty database. The app initialises all tables on startup (migration on boot). No manual SQL required. If tables already exist, migration is idempotent (no error on re-run).

**EC-6.4:** GitHub repository becomes public accidentally. The `.gitignore` and environment variable discipline (FR-6.07) must ensure no credentials or user data are exposed. The SQLite file (if used) must be explicitly excluded from git.

---

### OPEN QUESTIONS

**OQ-14:** Should the database be SQLite (with Railway persistent volume) or PostgreSQL (Railway add-on)?
- Owner: Dot (architecture decision)
- Trade-off: SQLite is simpler, zero cost, no separate service. PostgreSQL is more robust, supports concurrent writes, easier to backup. For single-user personal app, SQLite is likely sufficient — but Railway's persistent volume behaviour for SQLite must be confirmed.
- Blocker: FR-6.05 implementation decision

**OQ-15:** Does the user want a custom domain on Railway, or is the auto-generated Railway URL acceptable?
- Owner: User
- Impact: FR-6.04 HTTPS configuration, minor deployment config change

---

## OUT OF SCOPE — v1

The following are explicitly excluded from this requirements document and shall not be implemented without a new versioned requirements document:

1. **Multi-user access** — no shared accounts, no role-based permissions, no team features
2. **Automated broker data sync** — no Dime API, no Alpaca API integration, no live trade import
3. **Thai income beyond US stocks** — no Thai domestic income, Thai bonds, Thai mutual funds
4. **Cryptocurrency** — no BTC, ETH, or any digital asset tracking
5. **Options, futures, warrants, or derivatives** — equity positions only
6. **Tax filing submission** — the app produces data; filing is the user's manual act
7. **Automated database backup** — user is responsible via CSV export in v1
8. **Mobile-native app** — web browser only; mobile-responsive is desirable, not required
9. **Thai salary or employment income tracking** — user enters a total other-income figure manually; the app does not track salary transactions
10. **Multi-currency broker accounts** — only USD-denominated US stock positions via Dime
11. **Australian, UK, or other tax jurisdictions** — Thai Revenue Department only
12. **Investment advisory or "should I sell" features** — tax computation only, no trading recommendations
13. **Real-time trade alerts or notifications** — no push notifications, no email alerts
14. **Audit trail export for Revenue Department submission** — PND 90 summary is the output; formal audit trail formatting is out of scope

---

## DEPENDENCY MAP

```
Module 4 (Historical Onboarding)
    └── feeds into → Module 1 (Transaction Log) — lots and transactions

Module 1 (Transaction Log)
    └── feeds into → Module 2 (Remittance Log) — assessable gain pool
    └── feeds into → Module 5 (Dashboard) — portfolio summary

Module 2 (Remittance Log)
    └── feeds into → Module 3 (Tax Engine) — assessable income per tax year

Module 3 (Tax Engine)
    └── feeds into → Module 5 (Dashboard) — tax year summary, PND 90 view

Module 6 (Auth & Deployment)
    └── wraps all modules — authentication gate, deployment container
```

Build order recommendation for Dot: 6 → 4 → 1 → 2 → 3 → 5.
Start with auth shell and data persistence before any features. Historical onboarding before transaction logging, because it establishes the lot schema.

---

## OPEN QUESTIONS SUMMARY

| ID | Question | Owner | Blocker |
|----|----------|-------|---------|
| OQ-01 | BOT API endpoint — publicly accessible? | Dot | FR-1.07 |
| OQ-02 | Multi-currency dividend support needed? | User | FR-1.02 |
| OQ-03 | Disposal-level remittance linking needed? | User | FR-2.05 |
| OQ-04 | Exact PND 90 field mapping for capital gains / dividends / FTC | Josh + User | US-3.3 |
| OQ-05 | Corporate actions (splits, mergers) in scope for v1? | User | EC-1.2 |
| OQ-06 | Brokerage fees deductible from Thai capital gains? | Josh | FR-3.07 |
| OQ-07 | FIFO vs average cost — which does Thai Revenue accept? | Josh | FR-1.04 |
| OQ-08 | Thai capital loss carry-forward allowed? | Josh | EC-3.2 |
| OQ-09 | Are dividends subject to remittance-based assessability? | Josh | FR-3.03 |
| OQ-10 | Standard deductions to pre-populate in tax engine? | User | FR-3.07 |
| OQ-11 | Can user obtain per-trade CSV from Dime directly? | User (to test) | FR-4.04 |
| OQ-12 | Does user have Dime Annual Foreign Summary PDFs? | User | FR-4.04 alt path |
| OQ-13 | Reuse TideWatch backend for live prices or separate? | Dot | FR-5.02 |
| OQ-14 | SQLite with persistent volume vs Railway PostgreSQL? | Dot | FR-6.05 |
| OQ-15 | Custom domain needed on Railway? | User | FR-6.04 |

**Blockers before Dot can begin implementation:** OQ-01, OQ-07, OQ-09, OQ-14 are the highest-priority resolutions. OQ-04 must be resolved before the PND 90 summary view can be finalised, but it does not block the core data model.

---

*Document authored by Ice — Business Analyst & Requirements Specialist, Dugong Inc.*
*For implementation: Dot. For QA sign-off: Quinn. For delivery: Serena.*
*Next version (v2) triggered by: resolution of open questions, user feedback post-build, or Thai legislative changes.*
