import os
import csv
import io
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)
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
    txs = Transaction.query.order_by(Transaction.tx_date).all()
    gains, positions = compute_gains(txs, settings.cost_basis_method)

    yr = date.today().year
    yr_start, yr_end = date(yr, 1, 1), date(yr, 12, 31)

    ytd_gains = [g for g in gains if g['is_assessable'] and g['date'].year == yr]
    ytd_divs = Transaction.query.filter(
        Transaction.type == 'DIVIDEND',
        Transaction.tx_date.between(yr_start, yr_end),
    ).all()

    total_cg_thb = sum(g['gain_thb'] for g in ytd_gains)
    total_div_thb = sum(t.total_thb for t in ytd_divs if t.is_assessable)
    total_wht_thb = sum(t.withholding_tax_thb or 0 for t in ytd_divs if t.is_assessable)

    # Pre-compute position summaries so templates don't need complex filters
    position_summaries = []
    for symbol, lots in sorted(positions.items()):
        total_qty = sum(lot['qty'] for lot in lots)
        total_cost = sum(lot['qty'] * lot['per_share_thb'] for lot in lots)
        position_summaries.append({
            'symbol': symbol,
            'qty': total_qty,
            'avg_cost_thb': total_cost / total_qty if total_qty > 0 else 0,
            'total_cost_thb': total_cost,
        })

    recent = (Transaction.query
              .order_by(Transaction.tx_date.desc(), Transaction.id.desc())
              .limit(8).all())
    rems_ytd = Remittance.query.filter(Remittance.remit_date >= yr_start).all()
    total_remitted = sum(r.amount_thb for r in rems_ytd)

    return render_template('dashboard.html',
        position_summaries=position_summaries, year=yr,
        total_cg_thb=total_cg_thb, total_div_thb=total_div_thb,
        total_wht_thb=total_wht_thb, recent=recent,
        total_remitted=total_remitted, settings=settings,
    )

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

# ── CSV Import ────────────────────────────────────────────────────────────────

@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_csv():
    if request.method == 'POST':
        f = request.files.get('csv_file')
        if not f or not f.filename.endswith('.csv'):
            flash('Upload a .csv file.', 'danger')
            return redirect(url_for('import_csv'))

        exrate = float(request.form.get('exchange_rate') or 0)
        if exrate <= 0:
            flash('Enter a valid exchange rate for the import.', 'danger')
            return redirect(url_for('import_csv'))

        stream = io.StringIO(f.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)
        imported, errors = 0, []

        for i, row in enumerate(reader, 1):
            try:
                sym = (row.get('Symbol') or row.get('symbol', '')).strip().upper()
                if not sym:
                    continue

                acq_str = (row.get('Acquired Date') or row.get('acquired_date') or '').strip()
                dis_str = (row.get('Disposed Date') or row.get('disposed_date') or '').strip()
                cost = float((row.get('Cost') or row.get('cost') or '0').replace(',', ''))
                proceed = float((row.get('Proceed') or row.get('proceed') or '0').replace(',', ''))
                qty_raw = (row.get('Quantity') or row.get('quantity') or '1').replace(',', '')
                qty = float(qty_raw) if qty_raw else 1.0

                if acq_str:
                    acq_date = _parse_date(acq_str)
                    price = cost / qty if qty else cost
                    db.session.add(Transaction(
                        type='BUY', symbol=sym, tx_date=acq_date,
                        quantity=qty, price_usd=price, total_usd=cost,
                        fees_usd=0, exchange_rate=exrate, total_thb=cost * exrate,
                        notes='Imported from CSV',
                    ))
                    imported += 1

                if dis_str:
                    dis_date = _parse_date(dis_str)
                    price = proceed / qty if qty else proceed
                    db.session.add(Transaction(
                        type='SELL', symbol=sym, tx_date=dis_date,
                        quantity=qty, price_usd=price, total_usd=proceed,
                        fees_usd=0, exchange_rate=exrate, total_thb=proceed * exrate,
                        notes='Imported from CSV',
                    ))
                    imported += 1
            except Exception as e:
                errors.append(f'Row {i}: {e}')

        db.session.commit()
        flash(f'Imported {imported} transaction records.', 'success')
        for e in errors[:5]:
            flash(e, 'warning')
        return redirect(url_for('transactions'))

    return render_template('import_csv.html')


def _parse_date(s):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Cannot parse date: {s}')

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
