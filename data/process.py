#!/usr/bin/env python3
"""
process.py — Convert transactions_raw.json → transactions_import.json
Fetches historical USDTHB rates and merges dividend + withholding tax pairs.
"""

import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import yfinance as yf
except ImportError:
    print("Run: pip install yfinance")
    sys.exit(1)

RAW_FILE  = "transactions_raw.json"
OUT_FILE  = "transactions_import.json"
CUTOFF    = datetime(2024, 1, 1).date()

# ── Rate cache ────────────────────────────────────────────────────────────────

_cache = {}

def get_rate(date_obj):
    key = date_obj.strftime('%Y-%m-%d')
    if key in _cache:
        return _cache[key]
    ticker = yf.Ticker("USDTHB=X")
    for offset in range(7):
        d = date_obj + timedelta(days=offset)
        try:
            hist = ticker.history(
                start=d.strftime('%Y-%m-%d'),
                end=(d + timedelta(days=3)).strftime('%Y-%m-%d')
            )
            if not hist.empty:
                rate = round(float(hist['Close'].iloc[0]), 4)
                _cache[key] = rate
                return rate
        except Exception:
            continue
    print(f"  WARNING: no rate for {key}")
    return None

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(s):
    return datetime.strptime(s.strip(), '%d %b %Y').date()

def sort_key(t):
    for fmt in ('%d %b %Y %I:%M:%S %p', '%d %b %Y %H:%M:%S', '%d %b %Y'):
        try:
            return datetime.strptime(f"{t['date']} {t['time']}".strip(), fmt)
        except Exception:
            continue
    return datetime.strptime(t['date'], '%d %b %Y')

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(RAW_FILE) as f:
        raw = json.load(f)

    buys  = [t for t in raw if t['type'] == 'BUY']
    sells = [t for t in raw if t['type'] == 'SELL']
    divs  = [t for t in raw if t['type'] == 'DIVIDEND']
    whts  = [t for t in raw if t['type'] == 'DIVIDEND_WHT']

    print(f"Raw: {len(buys)} BUY  {len(sells)} SELL  {len(divs)} DIV  {len(whts)} WHT")

    # ── Merge WHT into dividends ──────────────────────────────────────────────
    wht_pool = defaultdict(list)
    for w in whts:
        wht_pool[w['symbol']].append({
            'date': parse_date(w['date']),
            'amount': w['amount_usd'],
            'used': False,
        })

    merged_divs = []
    for d in divs:
        ddate = parse_date(d['date'])
        wht_amount = 0.0
        for w in wht_pool.get(d['symbol'], []):
            if not w['used'] and abs((w['date'] - ddate).days) <= 2:
                wht_amount = w['amount']
                w['used'] = True
                break
        merged_divs.append({
            'symbol': d['symbol'], 'date': d['date'], 'time': d['time'],
            'date_obj': ddate, 'gross_usd': d['amount_usd'], 'wht_usd': wht_amount,
        })

    unmatched_wht = sum(1 for pool in wht_pool.values() for w in pool if not w['used'])
    if unmatched_wht:
        print(f"  Note: {unmatched_wht} WHT entries had no matching dividend (may be timing gap)")

    # ── Process BUYs ──────────────────────────────────────────────────────────
    print(f"\nFetching rates for {len(buys)} BUYs...")
    result_buys = []
    for i, tx in enumerate(buys):
        ddate   = parse_date(tx['date'])
        shares  = tx['shares'] or 0.0
        price   = tx['executed_price_usd'] or 0.0
        amt     = tx.get('total_amount')
        cur     = tx.get('total_currency')

        if cur == 'THB' and amt and shares > 0 and price > 0:
            total_usd = round(shares * price, 6)
            rate      = round(amt / total_usd, 4) if total_usd else get_rate(ddate)
            total_thb = amt
        elif cur == 'USD' and amt:
            total_usd = amt
            rate      = get_rate(ddate)
            total_thb = round(total_usd * rate, 2) if rate else None
        else:
            total_usd = round(shares * price, 6) if shares and price else None
            rate      = get_rate(ddate)
            total_thb = round(total_usd * rate, 2) if (total_usd and rate) else None

        result_buys.append({
            'type': 'BUY', 'symbol': tx['symbol'],
            'date': tx['date'], 'time': tx['time'],
            'shares': shares, 'price_usd': price,
            'total_usd': total_usd, 'exchange_rate': rate, 'total_thb': total_thb,
            'withholding_tax_usd': None, 'withholding_tax_thb': None,
        })
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(buys)}")

    # ── Process SELLs ─────────────────────────────────────────────────────────
    print(f"\nFetching rates for {len(sells)} SELLs...")
    result_sells = []
    for tx in sells:
        ddate     = parse_date(tx['date'])
        shares    = tx['shares'] or 0.0
        price     = tx['executed_price_usd'] or 0.0
        total_usd = round(shares * price, 6)
        rate      = get_rate(ddate)
        total_thb = round(total_usd * rate, 2) if rate else None

        result_sells.append({
            'type': 'SELL', 'symbol': tx['symbol'],
            'date': tx['date'], 'time': tx['time'],
            'shares': shares, 'price_usd': price,
            'total_usd': total_usd, 'exchange_rate': rate, 'total_thb': total_thb,
            'withholding_tax_usd': None, 'withholding_tax_thb': None,
        })

    # ── Process Dividends ─────────────────────────────────────────────────────
    print(f"\nFetching rates for {len(merged_divs)} Dividends...")
    result_divs = []
    for tx in merged_divs:
        rate      = get_rate(tx['date_obj'])
        gross_thb = round(tx['gross_usd'] * rate, 4) if rate else None
        wht_thb   = round(tx['wht_usd'] * rate, 4) if (rate and tx['wht_usd']) else None

        result_divs.append({
            'type': 'DIVIDEND', 'symbol': tx['symbol'],
            'date': tx['date'], 'time': tx['time'],
            'shares': None, 'price_usd': None,
            'total_usd': tx['gross_usd'], 'exchange_rate': rate, 'total_thb': gross_thb,
            'withholding_tax_usd': tx['wht_usd'] or None,
            'withholding_tax_thb': wht_thb,
        })

    # ── Sort and write ────────────────────────────────────────────────────────
    all_txs = sorted(result_buys + result_sells + result_divs, key=sort_key)

    with open(OUT_FILE, 'w') as f:
        json.dump(all_txs, f, indent=2, default=str)

    missing = [t for t in all_txs if not t.get('exchange_rate')]
    print(f"\n{'='*50}")
    print(f"Written {len(all_txs)} records → {OUT_FILE}")
    print(f"  BUY: {len(result_buys)}  SELL: {len(result_sells)}  DIVIDEND: {len(result_divs)}")
    if missing:
        print(f"\n  MISSING RATE ({len(missing)} records):")
        for t in missing:
            print(f"    {t['type']:10} {t['symbol']:8} {t['date']}")
    else:
        print("  All exchange rates resolved ✓")

if __name__ == '__main__':
    main()
