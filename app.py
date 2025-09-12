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

app.secret_key = 'Admin@Gk'  # Needed for sessions


# ----------------- Auth helper -----------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# ----------------- Models -----------------
class Bin(db.Model):
    # NOTE: SQLite doesn't enforce VARCHAR length; leaving larger to fit multi-letter prefixes + 5 digits
    id = db.Column(db.String(12), primary_key=True)
    puc = db.Column(db.String(100))
    farm_name = db.Column(db.String(100))
    commodity = db.Column(db.String(100))
    variety = db.Column(db.String(100))
    bin_class = db.Column(db.String(100))
    size = db.Column(db.String(100))  # NEW
    total_weight = db.Column(db.Float)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_tipped = db.Column(db.Boolean, default=False)
    tipped_weight = db.Column(db.Float, default=0.0)
    date = db.Column(db.Date)


class DropdownOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    field = db.Column(db.String(50))
    value = db.Column(db.String(100))


# ----------------- Startup (Flask 3-safe) -----------------
def ensure_size_column():
    """Add 'size' column to bin table if missing (SQLite quick-migration)."""
    with db.engine.begin() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bin)")]
        if 'size' not in cols:
            conn.exec_driver_sql("ALTER TABLE bin ADD COLUMN size VARCHAR(100)")


with app.app_context():
    db.create_all()
    ensure_size_column()


# ----------------- Routes -----------------
@app.route('/')
def dashboard():
    bins = Bin.query.all()
    grouped = {}
    for b in bins:
        key = (b.puc, b.commodity, b.variety, b.bin_class, b.farm_name)
        grouped.setdefault(key, []).append(b)

    summaries = []
    for (puc, commodity, variety, bin_class, farm_name), group in grouped.items():
        on_stock = [x for x in group if not x.is_tipped]
        tipped = [x for x in group if x.is_tipped]
        bin_ages = [(datetime.utcnow().date() - x.date).days for x in on_stock if x.date] or [0]
        summaries.append({
            'puc': puc,
            'farm_name': farm_name,
            'commodity': commodity,
            'variety': variety,
            'bin_class': bin_class,
            'total_bins': len(group),
            'bins_on_stock': len(on_stock),
            'bins_tipped': len(tipped),
            'total_weight': sum(x.total_weight or 0 for x in group),
            'tipped_weight': sum(x.tipped_weight or 0 for x in tipped),
            'oldest_bin_age': max(bin_ages),
        })
    return render_template('dashboard.html', stock_summary=summaries)


@app.route('/add_bins', methods=['GET', 'POST'])
def add_bins():
    if request.method == 'POST':
        num_bins = int(request.form['num_bins'])
        puc = request.form['puc']
        farm_name = request.form['farm_name']
        commodity = request.form['commodity']
        variety = request.form['variety']
        bin_class = request.form['bin_class']
        size = request.form['size']  # NEW
        total_weight = float(request.form['total_weight'])
        date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()

        bins = []

        # Build prefix from caps in farm name, fallback to first letter
        prefix = ''.join(c for c in farm_name if c.isupper())
        if not prefix:
            prefix = farm_name[0].upper()

        # Get next sequence number for this prefix
        existing_ids = Bin.query.filter(Bin.id.like(f"{prefix}%")).all()
        numbers = [int(b.id[len(prefix):]) for b in existing_ids if b.id[len(prefix):].isdigit()]
        next_number = (max(numbers) + 1) if numbers else 1

        for _ in range(num_bins):
            bin_id = f"{prefix}{next_number:05d}"
            next_number += 1

            new_bin = Bin(
                id=bin_id,
                puc=puc,
                farm_name=farm_name,
                commodity=commodity,
                variety=variety,
                bin_class=bin_class,
                size=size,                   # NEW
                total_weight=total_weight,
                date=date
            )
            db.session.add(new_bin)
            bins.append(new_bin)

        db.session.commit()
        return render_template('print_labels.html', bins=bins)

    dropdowns = {
        field: [opt.value for opt in DropdownOption.query.filter_by(field=field).all()]
        for field in ['puc', 'farm_name', 'commodity', 'variety', 'bin_class', 'size']  # include size
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


@app.route('/season_bins_tipped')
def season_bins_tipped():
    threshold = datetime.utcnow() - timedelta(hours=12)
    tipped_bins = Bin.query.filter(Bin.is_tipped == True, Bin.date_created < threshold).all()
    grouped = {}
    for b in tipped_bins:
        key = (b.puc, b.farm_name, b.commodity, b.variety, b.bin_class)
        grouped.setdefault(key, []).append(b)

    summary = []
    for (puc, farm, com, var, cls), group in grouped.items():
        summary.append({
            'puc': puc,
            'farm_name': farm,
            'commodity': com,
            'variety': var,
            'bin_class': cls,
            'total_bins': len(group),
            'total_weight': sum(x.tipped_weight or 0 for x in group)
        })
    return render_template('season_bins_tipped.html', summary=summary)


# ----------------- CSV Exports -----------------
def _csv_response(rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'PUC', 'Farm Name', 'Commodity', 'Variety', 'Class', 'Size', 'Total Weight', 'Tipped', 'Tipped Weight', 'Date'])
    for b in rows:
        writer.writerow([
            b.id, b.puc, b.farm_name, b.commodity, b.variety, b.bin_class,
            (b.size or ''), b.total_weight, b.is_tipped, b.tipped_weight, b.date
        ])
    output.seek(0)
    return Response(output, mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename={filename}"})


@app.route('/export_csv')
def export_csv():
    return _csv_response(Bin.query.all(), "bins_all.csv")


@app.route('/export_csv_on_stock')
def export_csv_on_stock():
    return _csv_response(Bin.query.filter_by(is_tipped=False).all(), "bins_on_stock.csv")


@app.route('/export_csv_tipped')
def export_csv_tipped():
    return _csv_response(Bin.query.filter_by(is_tipped=True).all(), "bins_tipped.csv")


@app.route('/export_csv_season')
def export_csv_season():
    threshold = datetime.utcnow() - timedelta(hours=12)
    rows = Bin.query.filter(Bin.is_tipped == True, Bin.date_created < threshold).all()
    return _csv_response(rows, "bins_season.csv")


# ----------------- Admin + Options -----------------
@app.route('/admin')
@login_required
def admin_panel():
    bins = Bin.query.order_by(Bin.date_created.desc()).all()
    return render_template('admin.html', bins=bins)


@app.route('/edit_bin/<bin_id>', methods=['GET', 'POST'])
@login_required
def edit_bin(bin_id):
    b = Bin.query.get(bin_id)
    if request.method == 'POST':
        b.puc = request.form['puc']
        b.farm_name = request.form['farm_name']
        b.commodity = request.form['commodity']
        b.variety = request.form['variety']
        b.bin_class = request.form['bin_class']
        b.size = request.form.get('size')  # NEW
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
    for field in ['puc', 'farm_name', 'commodity', 'variety', 'bin_class', 'size']:  # include size
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


# ----------------- DB + Auth -----------------
@app.route('/init_db')
def init_db():
    db.create_all()
    return "Database initialized."


@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'JD' and password == 'JD@groenkloof':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_panel'))
        else:
            flash('Invalid login. Please try again.')
    return render_template('admin_login.html')


@app.route('/admin_logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


if __name__ == '__main__':
    app.run(debug=True)
