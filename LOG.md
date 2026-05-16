# Project Log

**Project:** Dime Tax Tracker
*Running record of decisions, blockers, and notable events. Append only — never edit past entries.*

---

## Format
```
### YYYY-MM-DD — [Author]
[What happened, what was decided, what was learned]
```

---

<!-- Add entries below this line -->

### 2026-05-15 — Ice

Requirements Document v1 written and filed. Input: Josh's Research Brief on Thai tax rules and Dime/Alpaca data landscape.

Key decisions embedded in the requirements:
- Grandfathered flag lives on the LOT (acquired date), not on the disposal event. This is the single most important correctness requirement — a 2022 lot sold in 2025 has an assessable gain even though the lot is pre-2024.
- Tax trigger is the remittance date's calendar year, not the disposal date's year. Module 2 (Remittance Log) is therefore not a convenience feature — it is the legal mechanism.
- Build order recommended: Module 6 (auth shell) → Module 4 (historical onboarding + lot schema) → Module 1 (live transactions) → Module 2 (remittance) → Module 3 (tax engine) → Module 5 (dashboard).

15 open questions identified. Priority blockers for Dot to resolve before implementation: OQ-01 (BOT API), OQ-07 (FIFO vs average cost), OQ-09 (dividend assessability rule), OQ-14 (SQLite vs PostgreSQL).

OQ-04 (PND 90 field mapping) must be resolved before the PND 90 summary view is built but does not block the data model.

User must answer: OQ-02, OQ-03, OQ-05, OQ-10, OQ-11, OQ-12, OQ-15.
Josh must research: OQ-04, OQ-06, OQ-07, OQ-08, OQ-09.
Dot must confirm: OQ-01, OQ-13, OQ-14.
