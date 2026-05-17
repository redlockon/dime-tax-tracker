import os
import csv
import io
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify, Response)
from flask_sqlalchemy import SQLAlchemy
import yfinance as yf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-me')

db_url = os.environ.get('DATABASE_URL', 'sqlite:///dime_tracker.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
APP_PASSWORD = os.environ.get('APP_PASSWORD', 'changeme')
CUTOFF = date(2024, 1, 1)

# ── Models ────────────────────────────────────────────────────────────────────

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cost_basis_method = db.Column(db.String(10), default='FIFO')
    cost_basis_locked = db.Column(db.Boolean, default=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(10), nullable=False)       # BUY | SELL | DIVIDEND
    symbol = db.Column(db.String(20), nullable=False)
    tx_date = db.Column(db.Date, nullable=False)
    quantity = db.Column(db.Float, nullable=True)
    price_usd = db.Column(db.Float, nullable=True)
    total_usd = db.Column(db.Float, nullable=False)
    fees_usd = db.Column(db.Float, default=0.0)
    exchange_rate = db.Column(db.Float, nullable=False)   # USDTHB at tx_date
    total_thb = db.Column(db.Float, nullable=False)
    withholding_tax_usd = db.Column(db.Float, nullable=True)
    withholding_tax_thb = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_assessable(self):
        return self.tx_date >= CUTOFF


class PriceCache(db.Model):
    symbol    = db.Column(db.String(20), primary_key=True)
    price_usd = db.Column(db.Float)
    fetched_at = db.Column(db.DateTime)


class Remittance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    remit_date = db.Column(db.Date, nullable=False)
    amount_usd = db.Column(db.Float, nullable=True)
    exchange_rate = db.Column(db.Float, nullable=False)
    amount_thb = db.Column(db.Float, nullable=False)
    bank = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == APP_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        flash('Wrong password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/debug/prices')
def debug_prices():
    import traceback, time
    out = {}

    # Test 1: Ticker.fast_info (single symbol)
    t0 = time.time()
    try:
        out['ticker_AAPL'] = round(float(yf.Ticker('AAPL').fast_info.last_price), 4)
    except Exception as e:
        out['ticker_AAPL'] = f'ERROR: {e}'
    out['ticker_elapsed_s'] = round(time.time() - t0, 2)

    # Test 2: yf.download with a LIST (not string)
    t1 = time.time()
    try:
        raw = yf.download(['AAPL', 'MSFT'], period='2d', progress=False,
                          auto_adjust=True, threads=True)
        closes = raw['Close']
        out['download_AAPL'] = round(float(closes['AAPL'].dropna().iloc[-1]), 4)
        out['download_MSFT'] = round(float(closes['MSFT'].dropna().iloc[-1]), 4)
    except Exception as e:
        out['download_error'] = repr(e)
        out['download_raw_cols'] = str(getattr(raw, 'columns', 'N/A'))
    out['download_elapsed_s'] = round(time.time() - t1, 2)

    return jsonify(out)


@app.route('/health')
def health():
    count = Transaction.query.count()
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    db_type = 'postgresql' if 'postgresql' in db_uri else 'sqlite'
    return jsonify(ok=True, db=db_type, tx_count=count), 200

# ── Exchange Rate ─────────────────────────────────────────────────────────────

def get_usdthb_rate(for_date=None):
    try:
        ticker = yf.Ticker('USDTHB=X')
        if for_date:
            d = for_date if isinstance(for_date, date) else for_date.date()
            end = d + timedelta(days=5)
            hist = ticker.history(start=d.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
            if not hist.empty:
                return round(float(hist['Close'].iloc[0]), 4)
        return round(float(ticker.fast_info.last_price), 4)
    except Exception:
        return None


@app.route('/api/rate')
@login_required
def api_rate():
    d_str = request.args.get('date')
    d = None
    if d_str:
        try:
            d = datetime.strptime(d_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    rate = get_usdthb_rate(d)
    return jsonify({'rate': rate})

# ── Tax Engine ────────────────────────────────────────────────────────────────

THAI_BRACKETS = [
    (150_000,       0.00),
    (300_000,       0.05),
    (500_000,       0.10),
    (750_000,       0.15),
    (1_000_000,     0.20),
    (2_000_000,     0.25),
    (5_000_000,     0.30),
    (float('inf'), 0.35),
]


def thai_pit(net_income):
    tax, prev = 0.0, 0
    for bracket, rate in THAI_BRACKETS:
        if net_income <= prev:
            break
        tax += (min(net_income, bracket) - prev) * rate
        prev = bracket
    return tax


def get_settings():
    s = Settings.query.first()
    if not s:
        s = Settings()
        db.session.add(s)
        db.session.commit()
    return s


def compute_gains(transactions, method='FIFO'):
    by_symbol = {}
    for tx in transactions:
        if tx.type in ('BUY', 'SELL'):
            by_symbol.setdefault(tx.symbol, []).append(tx)

    all_gains, positions = [], {}
    for symbol, txs in by_symbol.items():
        txs = sorted(txs, key=lambda x: x.tx_date)
        fn = _fifo if method == 'FIFO' else _average
        gains, lots = fn(txs)
        all_gains.extend(gains)
        if lots:
            positions[symbol] = lots
    return all_gains, positions


def _fifo(txs):
    lots, gains = [], []
    for tx in txs:
        if tx.type == 'BUY':
            cost_thb = (tx.total_usd + (tx.fees_usd or 0)) * tx.exchange_rate
            lots.append({'date': tx.tx_date, 'qty': tx.quantity,
                         'per_share_thb': cost_thb / tx.quantity})
        elif tx.type == 'SELL':
            remaining = tx.quantity
            cost_basis = 0.0
            while remaining > 1e-6 and lots:
                lot = lots[0]
                take = min(lot['qty'], remaining)
                cost_basis += take * lot['per_share_thb']
                lot['qty'] -= take
                remaining -= take
                if lot['qty'] < 1e-6:
                    lots.pop(0)
            proceeds = tx.total_usd * tx.exchange_rate
            gains.append(_gain_record(tx, proceeds, cost_basis))
    return gains, lots


def _average(txs):
    total_qty, total_cost_thb, gains = 0.0, 0.0, []
    for tx in txs:
        if tx.type == 'BUY':
            total_qty += tx.quantity
            total_cost_thb += (tx.total_usd + (tx.fees_usd or 0)) * tx.exchange_rate
        elif tx.type == 'SELL' and total_qty > 1e-6:
            avg = total_cost_thb / total_qty
            cost_basis = tx.quantity * avg
            proceeds = tx.total_usd * tx.exchange_rate
            total_qty -= tx.quantity
            total_cost_thb -= cost_basis
            gains.append(_gain_record(tx, proceeds, cost_basis))
    lots = ([{'date': None, 'qty': total_qty,
               'per_share_thb': total_cost_thb / total_qty}]
            if total_qty > 1e-6 else [])
    return gains, lots


def _gain_record(tx, proceeds, cost_basis):
    return {
        'tx_id': tx.id, 'date': tx.tx_date, 'symbol': tx.symbol,
        'quantity': tx.quantity, 'proceeds_thb': proceeds,
        'cost_basis_thb': cost_basis, 'gain_thb': proceeds - cost_basis,
        'is_assessable': tx.tx_date >= CUTOFF,
    }

# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    settings = get_settings()
    yr = request.args.get('year', date.today().year, type=int)

    txs = Transaction.query.order_by(Transaction.tx_date, Transaction.id).all()
    gains, positions = compute_gains(txs, settings.cost_basis_method)

    # Cached prices
    price_map   = {p.symbol: p for p in PriceCache.query.all()}
    rate_entry  = price_map.get('__USDTHB__')
    current_rate = rate_entry.price_usd if rate_entry else None
    last_updated = rate_entry.fetched_at if rate_entry else None

    # Build position summaries with live market data
    position_summaries = []
    total_cost_thb  = 0.0
    total_value_thb = 0.0
    prices_available = False

    for symbol, lots in sorted(positions.items()):
        total_qty = sum(lot['qty'] for lot in lots)
        if total_qty < 0.0001:
            continue
        total_cost = sum(lot['qty'] * lot['per_share_thb'] for lot in lots)

        cached = price_map.get(symbol)
        cur_price = cached.price_usd if cached else None
        cur_value = round(cur_price * total_qty * current_rate, 2) if (cur_price and current_rate) else None
        unreal    = round(cur_value - total_cost, 2) if cur_value is not None else None
        unreal_pct = round(unreal / total_cost * 100, 2) if (unreal is not None and total_cost) else None

        if cur_value is not None:
            prices_available = True
            total_value_thb += cur_value

        total_cost_thb += total_cost
        position_summaries.append({
            'symbol': symbol, 'qty': total_qty,
            'avg_cost_thb': total_cost / total_qty,
            'total_cost_thb': total_cost,
            'current_price_usd': cur_price,
            'current_value_thb': cur_value,
            'unrealized_thb': unreal,
            'unrealized_pct': unreal_pct,
        })

    total_unrealized_thb = round(total_value_thb - total_cost_thb, 2) if prices_available else None

    # Tax section for selected year
    yr_start, yr_end = date(yr, 1, 1), date(yr, 12, 31)
    yr_gains = [g for g in gains if g['is_assessable'] and g['date'].year == yr]
    yr_divs  = Transaction.query.filter(
        Transaction.type == 'DIVIDEND',
        Transaction.tx_date.between(yr_start, yr_end),
        Transaction.tx_date >= CUTOFF,
    ).all()
    total_cg_thb  = sum(g['gain_thb'] for g in yr_gains)
    total_div_thb = sum(t.total_thb for t in yr_divs)
    total_wht_thb = sum(t.withholding_tax_thb or 0 for t in yr_divs)

    years = sorted(
        {int(r[0]) for r in db.session.query(db.extract('year', Transaction.tx_date)).all()},
        reverse=True,
    )

    return render_template('dashboard.html',
        position_summaries=position_summaries,
        total_cost_thb=total_cost_thb,
        total_value_thb=total_value_thb if prices_available else None,
        total_unrealized_thb=total_unrealized_thb,
        current_rate=current_rate, last_updated=last_updated,
        year=yr, years=years,
        total_cg_thb=total_cg_thb, total_div_thb=total_div_thb,
        total_wht_thb=total_wht_thb, settings=settings,
    )


@app.route('/api/refresh-prices', methods=['POST'])
@login_required
def refresh_prices():
    txs = Transaction.query.filter(
        Transaction.type.in_(['BUY', 'SELL'])
    ).order_by(Transaction.tx_date, Transaction.id).all()
    _, positions = compute_gains(txs, get_settings().cost_basis_method)
    symbols = [s for s, lots in positions.items() if sum(l['qty'] for l in lots) > 0.0001]

    now     = datetime.utcnow()
    fetched = 0
    rate    = None

    try:
        # yfinance requires BRK-B not BRK.B
        yf_map  = {s.replace('.', '-'): s for s in symbols}
        yf_syms = list(yf_map.keys())

        raw    = yf.download(yf_syms, period='2d', progress=False,
                             auto_adjust=True, threads=True)
        closes = raw['Close'] if len(yf_syms) > 1 else raw['Close'].rename(yf_syms[0])

        for yfsym, orig in yf_map.items():
            try:
                price = round(float(closes[yfsym].dropna().iloc[-1]), 4)
                entry = db.session.get(PriceCache, orig) or PriceCache(symbol=orig)
                entry.price_usd  = price
                entry.fetched_at = now
                db.session.merge(entry)
                fetched += 1
            except Exception:
                pass

        rate = get_usdthb_rate()
        if rate:
            entry = db.session.get(PriceCache, '__USDTHB__') or PriceCache(symbol='__USDTHB__')
            entry.price_usd  = rate
            entry.fetched_at = now
            db.session.merge(entry)

        db.session.commit()
    except Exception as e:
        flash(f'Price fetch failed: {e}', 'danger')
        return redirect(url_for('dashboard'))

    rate_str = f'฿{rate:.2f}' if rate else '—'
    flash(f'Prices updated for {fetched}/{len(symbols)} symbols · 1 USD = {rate_str}', 'success')
    return redirect(url_for('dashboard'))

# ── Transactions ──────────────────────────────────────────────────────────────

@app.route('/transactions')
@login_required
def transactions():
    yr = request.args.get('year', type=int)
    sym = request.args.get('symbol', '').upper()
    typ = request.args.get('type', '')

    q = Transaction.query
    if yr:
        q = q.filter(db.extract('year', Transaction.tx_date) == yr)
    if sym:
        q = q.filter(Transaction.symbol == sym)
    if typ:
        q = q.filter(Transaction.type == typ)
    txs = q.order_by(Transaction.tx_date.desc(), Transaction.id.desc()).all()

    years = sorted(
        {int(r[0]) for r in db.session.query(db.extract('year', Transaction.tx_date)).all()},
        reverse=True,
    )
    symbols = sorted(
        {r[0] for r in db.session.query(Transaction.symbol).all()}
    )
    return render_template('transactions.html', txs=txs, years=years, symbols=symbols,
                           fy=yr, fs=sym, ft=typ)


@app.route('/transactions/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    settings = get_settings()
    if request.method == 'POST':
        typ = request.form['type']
        symbol = request.form['symbol'].upper().strip()
        tx_date = datetime.strptime(request.form['tx_date'], '%Y-%m-%d').date()
        exrate = float(request.form['exchange_rate'])

        tx = Transaction(type=typ, symbol=symbol, tx_date=tx_date, exchange_rate=exrate)

        if typ in ('BUY', 'SELL'):
            qty = float(request.form['quantity'])
            price = float(request.form['price_usd'])
            fees = float(request.form.get('fees_usd') or 0)
            tx.quantity = qty
            tx.price_usd = price
            tx.fees_usd = fees
            tx.total_usd = qty * price
            tx.total_thb = tx.total_usd * exrate
            if typ == 'SELL' and not settings.cost_basis_locked:
                settings.cost_basis_locked = True
                db.session.add(settings)
        else:  # DIVIDEND
            tx.total_usd = float(request.form['total_usd'])
            wht = float(request.form.get('withholding_tax_usd') or 0)
            tx.withholding_tax_usd = wht
            tx.withholding_tax_thb = wht * exrate
            tx.total_thb = tx.total_usd * exrate

        tx.notes = request.form.get('notes', '').strip() or None
        db.session.add(tx)
        db.session.commit()
        flash(f'{typ} recorded for {symbol}.', 'success')
        return redirect(url_for('transactions'))

    return render_template('add_transaction.html',
                           today=date.today().isoformat(), settings=settings)


@app.route('/transactions/<int:tx_id>/delete', methods=['POST'])
@login_required
def delete_transaction(tx_id):
    tx = db.session.get(Transaction, tx_id) or db.session.get(Transaction, tx_id)
    if tx:
        db.session.delete(tx)
        db.session.commit()
        flash('Transaction deleted.', 'warning')
    return redirect(url_for('transactions'))

# ── Remittances ───────────────────────────────────────────────────────────────

@app.route('/remittances')
@login_required
def remittances():
    yr = request.args.get('year', type=int)
    q = Remittance.query
    if yr:
        q = q.filter(db.extract('year', Remittance.remit_date) == yr)
    rems = q.order_by(Remittance.remit_date.desc()).all()
    total_thb = sum(r.amount_thb for r in rems)
    years = sorted(
        {int(r[0]) for r in db.session.query(db.extract('year', Remittance.remit_date)).all()},
        reverse=True,
    )
    return render_template('remittances.html', rems=rems, total_thb=total_thb,
                           years=years, fy=yr)


@app.route('/remittances/add', methods=['GET', 'POST'])
@login_required
def add_remittance():
    if request.method == 'POST':
        remit_date = datetime.strptime(request.form['remit_date'], '%Y-%m-%d').date()
        exrate = float(request.form['exchange_rate'])
        amount_thb = float(request.form['amount_thb'])
        amount_usd_raw = request.form.get('amount_usd', '').strip()
        amount_usd = float(amount_usd_raw) if amount_usd_raw else None
        r = Remittance(
            remit_date=remit_date, exchange_rate=exrate, amount_thb=amount_thb,
            amount_usd=amount_usd,
            bank=request.form.get('bank', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
        )
        db.session.add(r)
        db.session.commit()
        flash('Remittance logged.', 'success')
        return redirect(url_for('remittances'))
    return render_template('add_remittance.html', today=date.today().isoformat())


@app.route('/remittances/<int:r_id>/delete', methods=['POST'])
@login_required
def delete_remittance(r_id):
    r = db.session.get(Remittance, r_id)
    if r:
        db.session.delete(r)
        db.session.commit()
        flash('Remittance deleted.', 'warning')
    return redirect(url_for('remittances'))

# ── Tax Summary ───────────────────────────────────────────────────────────────

@app.route('/tax')
@login_required
def tax_summary():
    settings = get_settings()
    yr = request.args.get('year', date.today().year, type=int)
    other_income = request.args.get('other_income', 0.0, type=float)

    txs = Transaction.query.order_by(Transaction.tx_date).all()
    all_gains, _ = compute_gains(txs, settings.cost_basis_method)

    yr_gains = [g for g in all_gains if g['date'].year == yr and g['is_assessable']]
    total_cg = sum(g['gain_thb'] for g in yr_gains)

    yr_divs = Transaction.query.filter(
        Transaction.type == 'DIVIDEND',
        Transaction.tx_date.between(date(yr, 1, 1), date(yr, 12, 31)),
        Transaction.tx_date >= CUTOFF,
    ).order_by(Transaction.tx_date).all()

    total_div = sum(t.total_thb for t in yr_divs)
    total_wht = sum(t.withholding_tax_thb or 0 for t in yr_divs)

    assessable = total_cg + total_div

    # Thai PIT — incremental tax on investment income above other_income
    tax_total = thai_pit(other_income + assessable)
    tax_other = thai_pit(other_income)
    tax_on_inv = tax_total - tax_other
    credit = min(total_wht, tax_on_inv)
    net_tax = max(0.0, tax_on_inv - credit)

    years = sorted(
        {int(r[0]) for r in db.session.query(db.extract('year', Transaction.tx_date)).all()},
        reverse=True,
    )
    if yr not in years:
        years.insert(0, yr)

    return render_template('tax_summary.html',
        year=yr, years=years, other_income=other_income,
        yr_gains=yr_gains, total_cg=total_cg,
        yr_divs=yr_divs, total_div=total_div, total_wht=total_wht,
        assessable=assessable, tax_on_inv=tax_on_inv,
        credit=credit, net_tax=net_tax, settings=settings,
    )

# ── Tax Export ────────────────────────────────────────────────────────────────

@app.route('/tax/export')
@login_required
def export_tax():
    yr = request.args.get('year', date.today().year, type=int)
    txs = Transaction.query.order_by(Transaction.tx_date, Transaction.id).all()
    gains, _ = compute_gains(txs, get_settings().cost_basis_method)

    yr_gains = [g for g in gains if g['is_assessable'] and g['date'].year == yr]
    yr_divs  = Transaction.query.filter(
        Transaction.type == 'DIVIDEND',
        Transaction.tx_date.between(date(yr, 1, 1), date(yr, 12, 31)),
        Transaction.tx_date >= CUTOFF,
    ).order_by(Transaction.tx_date).all()

    out = io.StringIO()
    w   = csv.writer(out)

    w.writerow([f'Capital Gains {yr} — FIFO, assessable only (pre-2024 excluded)'])
    w.writerow(['Date', 'Symbol', 'Shares', 'Proceeds (THB)', 'Cost Basis (THB)', 'Gain/Loss (THB)'])
    for g in yr_gains:
        w.writerow([g['date'], g['symbol'], f"{g['quantity']:.6f}",
                    f"{g['proceeds_thb']:.2f}", f"{g['cost_basis_thb']:.2f}", f"{g['gain_thb']:.2f}"])
    total_cg = sum(g['gain_thb'] for g in yr_gains)
    w.writerow(['', '', 'TOTAL', '', '', f'{total_cg:.2f}'])

    w.writerow([])

    w.writerow([f'Dividends {yr} — assessable only'])
    w.writerow(['Date', 'Symbol', 'Gross (THB)', 'US WHT (THB)', 'Net (THB)'])
    for t in yr_divs:
        wht = t.withholding_tax_thb or 0
        w.writerow([t.tx_date, t.symbol, f'{t.total_thb:.2f}', f'{wht:.2f}', f'{t.total_thb - wht:.2f}'])
    total_div = sum(t.total_thb for t in yr_divs)
    total_wht = sum(t.withholding_tax_thb or 0 for t in yr_divs)
    w.writerow(['', 'TOTAL', f'{total_div:.2f}', f'{total_wht:.2f}', f'{total_div - total_wht:.2f}'])

    w.writerow([])
    w.writerow(['ASSESSABLE INCOME SUMMARY'])
    w.writerow(['Capital Gains (THB)', f'{total_cg:.2f}'])
    w.writerow(['Dividends (THB)', f'{total_div:.2f}'])
    w.writerow(['US WHT Credit (THB)', f'{total_wht:.2f}'])
    w.writerow(['Net Assessable (THB)', f'{total_cg + total_div:.2f}'])

    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=tax_{yr}.csv'})

# ── Settings ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    s = get_settings()
    if request.method == 'POST' and not s.cost_basis_locked:
        s.cost_basis_method = request.form.get('cost_basis_method', 'FIFO')
        db.session.commit()
        flash('Settings saved.', 'success')
    elif request.method == 'POST' and s.cost_basis_locked:
        flash('Cost basis method is locked — a SELL transaction has already been recorded.', 'warning')
    return render_template('settings.html', s=s)

# ── Template Filters ──────────────────────────────────────────────────────────

@app.template_filter('thb')
def fmt_thb(v):
    if v is None:
        return '—'
    return f'฿{v:,.2f}'

@app.template_filter('usd')
def fmt_usd(v):
    if v is None:
        return '—'
    return f'${v:,.4f}'

@app.template_filter('usd2')
def fmt_usd2(v):
    if v is None:
        return '—'
    return f'${v:,.2f}'

@app.template_filter('pct')
def fmt_pct(v):
    if v is None:
        return '—'
    sign = '+' if v >= 0 else ''
    return f'{sign}{v:,.2f}%'

@app.template_filter('num')
def fmt_num(v):
    if v is None:
        return '—'
    return f'{v:,.4f}'

# ── Bootstrap ─────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
