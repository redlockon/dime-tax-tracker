#!/usr/bin/env python3
"""
import_to_db.py — Load transactions_import.json into the Dime Tax Tracker database.

Run from the project root (one level up from data/):
    DATABASE_URL=<url> python data/import_to_db.py

Or for local SQLite (default):
    python data/import_to_db.py
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import app and models
from app import app, db, Transaction, Settings

IMPORT_FILE = HERE / "transactions_import.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(s):
    for fmt in ('%d %b %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not IMPORT_FILE.exists():
        print(f"ERROR: {IMPORT_FILE} not found. Run process.py first.")
        sys.exit(1)

    with open(IMPORT_FILE) as f:
        records = json.load(f)

    skipped_no_rate   = []
    skipped_no_total  = []
    imported          = []
    errors            = []

    with app.app_context():
        db.create_all()

        # Ensure Settings row exists
        if not Settings.query.first():
            db.session.add(Settings())
            db.session.commit()

        existing_count = Transaction.query.count()
        if existing_count > 0:
            print(f"WARNING: database already has {existing_count} transaction(s).")
            ans = input("Append anyway? [y/N] ").strip().lower()
            if ans != 'y':
                print("Aborted.")
                sys.exit(0)

        for rec in records:
            tx_type       = rec.get('type')
            symbol        = rec.get('symbol', '').strip()
            date_str      = rec.get('date', '')
            shares        = to_float(rec.get('shares'))
            price_usd     = to_float(rec.get('price_usd'))
            total_usd     = to_float(rec.get('total_usd'))
            exchange_rate = to_float(rec.get('exchange_rate'))
            total_thb     = to_float(rec.get('total_thb'))
            wht_usd       = to_float(rec.get('withholding_tax_usd'))
            wht_thb       = to_float(rec.get('withholding_tax_thb'))

            if not exchange_rate:
                skipped_no_rate.append(f"{tx_type:10} {symbol:8} {date_str}")
                continue

            if total_usd is None or total_thb is None:
                skipped_no_total.append(f"{tx_type:10} {symbol:8} {date_str}")
                continue

            try:
                tx_date = parse_date(date_str)
            except ValueError as e:
                errors.append(str(e))
                continue

            tx = Transaction(
                type=tx_type,
                symbol=symbol,
                tx_date=tx_date,
                quantity=shares,
                price_usd=price_usd,
                total_usd=total_usd,
                fees_usd=0.0,
                exchange_rate=exchange_rate,
                total_thb=total_thb,
                withholding_tax_usd=wht_usd,
                withholding_tax_thb=wht_thb,
                notes='imported',
            )
            db.session.add(tx)
            imported.append(f"{tx_type:10} {symbol:8} {date_str}")

        db.session.commit()

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"Imported  : {len(imported)}")
    print(f"Skipped (no rate)  : {len(skipped_no_rate)}")
    print(f"Skipped (no total) : {len(skipped_no_total)}")
    if errors:
        print(f"Errors    : {len(errors)}")
        for e in errors:
            print(f"  {e}")
    print(f"{'='*55}")

    if skipped_no_rate:
        print(f"\nRecords with missing exchange rate ({len(skipped_no_rate)}):")
        for s in skipped_no_rate:
            print(f"  {s}")
        print("  → Re-run process.py or edit transactions_import.json to fill rates.")

    if skipped_no_total:
        print(f"\nRecords with missing totals ({len(skipped_no_total)}):")
        for s in skipped_no_total:
            print(f"  {s}")

    print("\nDone.")


if __name__ == '__main__':
    main()
