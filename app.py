# app.py
from flask import Flask, render_template, request, redirect, url_for, send_file, Response, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import io
import csv
import barcode
from barcode.writer import ImageWriter
from functools import wraps

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bins.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.secret_key = 'Admin@Gk'


# ----------------- Auth Decorator -----------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# ----------------- Models -----------------
class Bin(db.Model):
    id = db.Column(db.String(12), primary_key=True)
    run_number = db.Column(db.String(50))  # NEW
    puc = db.Column(db.String(100))
    farm_name = db.Column(db.String(100))
    commodity = db.Column(db.String(100))
    variety = db.Column(db.String(100))
    bin_class = db.Column(db.String(100))
    size = db.Column(db.String(100))
    total_weight = db.Column(db.Float)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_tipped = db.Column(db.Boolean, default=False)
    tipped_weight = db.Column(db.Float, default=0.0)
    date = db.Column(db.Date)


class DropdownOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    field = db.Column(db.String(50))
    value = db.Column(db.String(100))


# ----------------- Init + Column Migration -----------------
def ensure_column_exists():
    with db.engine.begin() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bin)")]
        if 'run_number' not in cols:
            conn.exec_driver_sql("ALTER TABLE bin ADD COLUMN run_number VARCHAR(50)")
        if 'size' not in cols:
            conn.exec_driver_sql("ALTER TABLE bin ADD COLUMN size VARCHAR(100)")


with app.app_context():
    db.create_all()
    ensure_column_exists()


# ----------------- Routes -----------------
@app.route('/')
@login_required
def dashboard():
    bins = Bin.query.filter_by(is_tipped=False).all()
    grouped = {}
    for b in bins:
        key = (b.run_number, b.puc, b.commodity, b.variety, b.bin_class, b.farm_name)
        grouped.setdefault(key, []).append(b)

    summaries = []
    for (run, puc, com, var, cls, farm), group in grouped.items():
        ages = [(datetime.utcnow().date() - x.date).days for x in group if x.date]
        summaries.append({
            'run_number': run,
            'puc': puc,
            'commodity': com,
            'variety': var,
            'bin_class': cls,
            'farm_name': farm,
            'bins_on_stock': len(group),
            'oldest_bin_age': max(ages or [0]),
        })
    return render_template('dashboard.html', stock_summary=summaries)


@app.route('/season_bins_tipped')
@login_required
def season_bins_tipped():
    threshold = datetime.utcnow() - timedelta(hours=1)
    bins = Bin.query.filter(Bin.is_tipped == True, Bin.date_created < threshold).all()
    grouped = {}
    for b in bins:
        key = (b.run_number, b.puc, b.commodity, b.variety, b.bin_class, b.farm_name)
        grouped.setdefault(key, []).append(b)

    summary = []
    for (run, puc, com, var, cls, farm), group in grouped.items():
        summary.append({
            'run_number': run,
            'puc': puc,
            'commodity': com,
            'variety': var,
            'bin_class': cls,
            'farm_name': farm,
            'bins_tipped': len(group),
            'tipped_weight': sum(x.tipped_weight or 0 for x in group)
        })
    return render_template('season_bins_tipped.html', summary=summary)


@app.route('/add_bins', methods=['GET', 'POST'])
@login_required
def add_bins():
    if request.method == 'POST':
        num_bins = int(request.form['num_bins'])
        run_number = request.form['run_number']
        puc = request.form['puc']
        farm_name = request.form['farm_name']
        commodity = request.form['commodity']
        variety = request.form['variety']
        bin_class = request.form['bin_class']
        size = request.form['size']
        total_weight = float(request.form['total_weight'])
        date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()

        prefix = ''.join(c for c in farm_name if c.isupper()) or farm_name[0].upper()
        existing_ids = Bin.query.filter(Bin.id.like(f"{prefix}%")).all()
        numbers = [int(b.id[len(prefix):]) for b in existing_ids if b.id[len(prefix):].isdigit()]
        next_number = (max(numbers) + 1) if numbers else 1

        bins = []
        for _ in range(num_bins):
            bin_id = f"{prefix}{next_number:05d}"
            next_number += 1
            b = Bin(
                id=bin_id,
                run_number=run_number,
                puc=puc,
                farm_name=farm_name,
                commodity=commodity,
                variety=variety,
                bin_class=bin_class,
                size=size,
                total_weight=total_weight,
                date=date
            )
            db.session.add(b)
            bins.append(b)

        db.session.commit()
        return render_template('print_labels.html', bins=bins)

    dropdowns = {
        field: [opt.value for opt in DropdownOption.query.filter_by(field=field).all()]
        for field in ['run_number', 'puc', 'farm_name', 'commodity', 'variety', 'bin_class', 'size']
    }
    return render_template('add_bins.html', dropdowns=dropdowns)


@app.route('/barcode/<bin_id>')
def barcode_image(bin_id):
    CODE128 = barcode.get_barcode_class('code128')
    barcode_obj = CODE128(bin_id, writer=ImageWriter())
    buffer = io.BytesIO()
    barcode_obj.write(buffer)
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png')


@app.route('/mark_tipped', methods=['GET', 'POST'])
@login_required
def mark_tipped():
    if request.method == 'POST':
        bin_id = request.form['bin_id']
        b = Bin.query.get(bin_id)
        if b and not b.is_tipped:
            b.is_tipped = True
            b.tipped_weight = b.total_weight
            db.session.commit()
        return redirect(url_for('mark_tipped'))
    bins = Bin.query.all()
    return render_template('mark_tipped.html', bins=bins)


@app.route('/admin')
@login_required
def admin_panel():
    bins = Bin.query.order_by(Bin.date_created.desc()).all()
    return render_template('admin.html', bins=bins)


@app.route('/manage_options', methods=['GET', 'POST'])
@login_required
def manage_options():
    if request.method == 'POST':
        field = request.form['field']
        value = request.form['value']
        if not DropdownOption.query.filter_by(field=field, value=value).first():
            db.session.add(DropdownOption(field=field, value=value))
            db.session.commit()
        return redirect(url_for('manage_options'))

    options = {}
    for field in ['run_number', 'puc', 'farm_name', 'commodity', 'variety', 'bin_class', 'size']:
        options[field] = DropdownOption.query.filter_by(field=field).all()
    return render_template('manage_options.html', options=options)


@app.route('/delete_option/<int:option_id>')
@login_required
def delete_option(option_id):
    opt = DropdownOption.query.get(option_id)
    if opt:
        db.session.delete(opt)
        db.session.commit()
    return redirect(url_for('manage_options'))


@app.route('/edit_bin/<bin_id>', methods=['GET', 'POST'])
@login_required
def edit_bin(bin_id):
    b = Bin.query.get(bin_id)
    if request.method == 'POST':
        b.run_number = request.form['run_number']
        b.puc = request.form['puc']
        b.farm_name = request.form['farm_name']
        b.commodity = request.form['commodity']
        b.variety = request.form['variety']
        b.bin_class = request.form['bin_class']
        b.size = request.form.get('size')
        b.total_weight = float(request.form['total_weight'])
        b.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        db.session.commit()
        return redirect(url_for('admin_panel'))
    return render_template('edit_bin.html', bin=b)


@app.route('/delete_bin/<bin_id>', methods=['POST'])
@login_required
def delete_bin(bin_id):
    b = Bin.query.get(bin_id)
    if b:
        db.session.delete(b)
        db.session.commit()
    return redirect(url_for('admin_panel'))


@app.route('/reprint/<bin_id>')
@login_required
def reprint_label(bin_id):
    b = Bin.query.get(bin_id)
    return render_template('print_labels.html', bins=[b])


@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'JD' and password == 'JD@groenkloof':
            session['admin_logged_in'] = True
            return redirect(url_for('dashboard'))
        flash('Invalid login.')
    return render_template('admin_login.html')


@app.route('/admin_logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/init_db')
def init_db():
    db.create_all()
    return "DB initialized."


if __name__ == '__main__':
    app.run(debug=True)
