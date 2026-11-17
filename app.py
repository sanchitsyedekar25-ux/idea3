"""Surplus Food Management System - Flask app (SQLite; swap db()/q()/run() for MySQL later)."""
import os, re, sqlite3
from datetime import date
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-for-production")
# Serverless hosts (Vercel) only allow writing to /tmp; data there is temporary.
DB = "/tmp/surplus.db" if os.environ.get("VERCEL") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "surplus.db")
STATUSES = ["Pending Verification", "Verified", "Volunteer Assigned", "Food Collected", "Distributed", "Rejected"]
HELP = ["Food Collection", "Transportation", "Packing", "Distribution"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---------- Database helpers (the only place that touches SQLite) ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows

def run(sql, args=()):
    cur = db().execute(sql, args); db().commit(); return cur.lastrowid

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password_hash TEXT);
CREATE TABLE IF NOT EXISTS restaurant_hotel(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, contact_person TEXT,
  phone TEXT, email TEXT UNIQUE, address TEXT, password_hash TEXT);
CREATE TABLE IF NOT EXISTS volunteer(id INTEGER PRIMARY KEY, name TEXT, phone TEXT, email TEXT UNIQUE, address TEXT,
  area TEXT, days TEXT, avail_time TEXT, help_type TEXT, password_hash TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS food_donation(id INTEGER PRIMARY KEY, restaurant_id INTEGER REFERENCES restaurant_hotel(id),
  contact_person TEXT, phone TEXT, email TEXT, food_type TEXT, quantity REAL, unit TEXT, servings INTEGER,
  pickup_location TEXT, pickup_date TEXT, pickup_time TEXT, prepared_at TEXT, notes TEXT,
  status TEXT DEFAULT 'Pending Verification', volunteer_id INTEGER REFERENCES volunteer(id), task_accepted INTEGER DEFAULT 0,
  distribution_location TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS beneficiary(id INTEGER PRIMARY KEY, code TEXT, name TEXT, phone TEXT, address TEXT,
  family_members INTEGER, pref_location TEXT, pref_time TEXT, reg_date TEXT, status TEXT DEFAULT 'Active');
CREATE TABLE IF NOT EXISTS distribution(id INTEGER PRIMARY KEY, donation_id INTEGER REFERENCES food_donation(id),
  volunteer_id INTEGER REFERENCES volunteer(id), beneficiary_id INTEGER REFERENCES beneficiary(id),
  quantity TEXT, location TEXT, dist_date TEXT, dist_time TEXT, status TEXT DEFAULT 'Completed');
"""

def init_db():
    con = sqlite3.connect(DB); con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM admin").fetchone():  # demo accounts
        con.execute("INSERT INTO admin(name,email,password_hash) VALUES(?,?,?)",
                    ("Office Staff", "admin@surplusfood.org", generate_password_hash("admin123")))
        con.execute("INSERT INTO restaurant_hotel(name,kind,contact_person,phone,email,address,password_hash) VALUES(?,?,?,?,?,?,?)",
                    ("Green Leaf Restaurant", "Restaurant", "Ravi Shah", "9876543210", "demo@restaurant.com",
                     "Station Road, Bhayandar", generate_password_hash("demo123")))
    con.commit(); con.close()

# ---------- Validation / auth helpers ----------
def phone_ok(p): return bool(re.fullmatch(r"\+?\d{10,13}", re.sub(r"[\s-]", "", p)))
def email_ok(e): return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e))
def positive(v):
    try: return float(v) > 0
    except ValueError: return False

def check(d, optional=()):
    errs = []
    if any(not v for k, v in d.items() if k not in optional): errs.append("Please fill in all required fields.")
    if d.get("email") and not email_ok(d["email"]): errs.append("Enter a valid email address.")
    if d.get("phone") and not phone_ok(d["phone"]): errs.append("Enter a valid phone number (10-13 digits).")
    return errs

def fields(*names): return {n: request.form.get(n, "").strip() for n in names}
def show(errs):
    for e in errs: flash(e, "error")
    return bool(errs)

def needs(role):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            if session.get("role") != role:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login", role=role))
            return f(*a, **k)
        return wrapper
    return deco

# ---------- Public pages ----------
@app.route("/")
def home():
    stats = {
        "food": q("SELECT COALESCE(SUM(quantity),0) c FROM food_donation WHERE status!='Rejected'", one=True)["c"],
        "meals": q("SELECT COALESCE(SUM(servings),0) c FROM food_donation WHERE status='Distributed'", one=True)["c"],
        "vols": q("SELECT COUNT(*) c FROM volunteer", one=True)["c"],
        "rests": q("SELECT COUNT(*) c FROM restaurant_hotel", one=True)["c"]}
    return render_template("index.html", stats=stats)

@app.route("/how-it-works")
def how(): return render_template("how.html")

LOGINS = {"admin": ("admin", "admin_dashboard"), "restaurant": ("restaurant_hotel", "restaurant_dashboard"),
          "volunteer": ("volunteer", "volunteer_dashboard")}

@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):
    if role not in LOGINS: abort(404)
    table, dest = LOGINS[role]
    if request.method == "POST":
        u = q(f"SELECT * FROM {table} WHERE email=?", (request.form.get("email", "").strip().lower(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear(); session.update(role=role, uid=u["id"], name=u["name"])
            return redirect(url_for(dest))
        flash("Incorrect email or password. Please try again.", "error")
    return render_template("login.html", role=role)

@app.route("/logout")
def logout():
    session.clear(); flash("You have been logged out.", "ok"); return redirect(url_for("home"))

@app.route("/volunteer/register", methods=["GET", "POST"])
def volunteer_register():
    if request.method == "POST":
        d = fields("name", "phone", "email", "address", "area", "avail_time", "help_type", "password", "confirm")
        d["email"] = d["email"].lower(); days = request.form.getlist("days")
        errs = check(d)
        if not days: errs.append("Select at least one available day.")
        if d["password"] != d["confirm"]: errs.append("Passwords do not match.")
        if len(d["password"]) < 6: errs.append("Password must be at least 6 characters.")
        if not show(errs):
            try:
                run("INSERT INTO volunteer(name,phone,email,address,area,days,avail_time,help_type,password_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                    (d["name"], d["phone"], d["email"], d["address"], d["area"], ", ".join(days), d["avail_time"],
                     d["help_type"], generate_password_hash(d["password"])))
                flash("Registration successful! Thank you for volunteering. You can now log in.", "ok")
                return redirect(url_for("login", role="volunteer"))
            except sqlite3.IntegrityError:
                flash("This email is already registered.", "error")
    return render_template("volunteer_register.html", HELP=HELP, DAYS=DAYS, f=request.form)

# ---------- Restaurant / Hotel ----------
@app.route("/donate", methods=["GET", "POST"])
@needs("restaurant")
def donate():
    r = q("SELECT * FROM restaurant_hotel WHERE id=?", (session["uid"],), one=True)
    if request.method == "POST":
        d = fields("contact_person", "phone", "email", "food_type", "quantity", "unit", "servings",
                   "pickup_location", "pickup_date", "pickup_time", "prepared_at", "notes")
        errs = check(d, optional=("notes",))
        if d["quantity"] and not positive(d["quantity"]): errs.append("Quantity must be greater than zero.")
        if d["servings"] and not (d["servings"].isdigit() and int(d["servings"]) > 0):
            errs.append("Number of people served must be a positive whole number.")
        if not show(errs):
            run("""INSERT INTO food_donation(restaurant_id,contact_person,phone,email,food_type,quantity,unit,servings,
                   pickup_location,pickup_date,pickup_time,prepared_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["id"], d["contact_person"], d["phone"], d["email"], d["food_type"], float(d["quantity"]), d["unit"],
                 int(d["servings"]), d["pickup_location"], d["pickup_date"], d["pickup_time"], d["prepared_at"], d["notes"]))
            flash("Food donation submitted successfully. Status: Pending Verification.", "ok")
            return redirect(url_for("restaurant_donations"))
    return render_template("donate.html", r=r, f=request.form, today=date.today().isoformat())

@app.route("/restaurant/dashboard")
@needs("restaurant")
def restaurant_dashboard():
    c = lambda w="": q(f"SELECT COUNT(*) c FROM food_donation WHERE restaurant_id=? {w}", (session["uid"],), one=True)["c"]
    s = dict(total=c(), pending=c("AND status='Pending Verification'"), verified=c("AND status='Verified'"),
             done=c("AND status='Distributed'"))
    return render_template("restaurant_dashboard.html", s=s)

@app.route("/restaurant/donations")
@needs("restaurant")
def restaurant_donations():
    rows = q("SELECT * FROM food_donation WHERE restaurant_id=? ORDER BY id DESC", (session["uid"],))
    return render_template("restaurant_donations.html", rows=rows, STATUSES=STATUSES)

# ---------- Volunteer ----------
@app.route("/volunteer/dashboard")
@needs("volunteer")
def volunteer_dashboard():
    tasks = q("""SELECT f.*, r.name rname FROM food_donation f JOIN restaurant_hotel r ON r.id=f.restaurant_id
                 WHERE f.volunteer_id=? ORDER BY f.id DESC""", (session["uid"],))
    s = dict(total=len(tasks), done=sum(t["status"] == "Distributed" for t in tasks))
    s["pending"] = s["total"] - s["done"]
    return render_template("volunteer_dashboard.html", tasks=tasks, s=s, STATUSES=STATUSES)

@app.route("/volunteer/task/<int:i>/<action>", methods=["POST"])
@needs("volunteer")
def task(i, action):
    t = q("SELECT * FROM food_donation WHERE id=? AND volunteer_id=?", (i, session["uid"]), one=True)
    if not t: abort(404)
    if action == "accept" and t["status"] == "Volunteer Assigned":
        run("UPDATE food_donation SET task_accepted=1 WHERE id=?", (i,)); flash("Task accepted. Thank you!", "ok")
    elif action == "collect" and t["status"] == "Volunteer Assigned":
        run("UPDATE food_donation SET status='Food Collected', task_accepted=1 WHERE id=?", (i,)); flash("Marked as collected.", "ok")
    elif action == "deliver" and t["status"] == "Food Collected":
        run("UPDATE food_donation SET status='Distributed' WHERE id=?", (i,)); flash("Marked as delivered. Great work!", "ok")
    else: flash("That action is not available for this task yet.", "error")
    return redirect(url_for("volunteer_dashboard"))

# ---------- Admin ----------
def admin_page(page, **kw): return render_template("admin.html", page=page, STATUSES=STATUSES, **kw)

@app.route("/admin")
@needs("admin")
def admin_dashboard():
    c = lambda sql: q(sql, one=True)["c"]
    s = [("Total Food Donations", c("SELECT COUNT(*) c FROM food_donation"), "🍱"),
         ("Pending Donations", c("SELECT COUNT(*) c FROM food_donation WHERE status='Pending Verification'"), "⏳"),
         ("Verified Donations", c("SELECT COUNT(*) c FROM food_donation WHERE status='Verified'"), "✅"),
         ("Total Volunteers", c("SELECT COUNT(*) c FROM volunteer"), "🤝"),
         ("Total Beneficiaries", c("SELECT COUNT(*) c FROM beneficiary"), "👨‍👩‍👧"),
         ("Meals Distributed", c("SELECT COALESCE(SUM(servings),0) c FROM food_donation WHERE status='Distributed'"), "🍽️")]
    recent = q("SELECT f.*, r.name rname FROM food_donation f JOIN restaurant_hotel r ON r.id=f.restaurant_id ORDER BY f.id DESC LIMIT 5")
    return admin_page("dashboard", s=s, rows=recent)

@app.route("/admin/donations")
@needs("admin")
def admin_donations():
    term, st = request.args.get("q", "").strip(), request.args.get("status", "")
    sql = "SELECT f.*, r.name rname, v.name vname FROM food_donation f JOIN restaurant_hotel r ON r.id=f.restaurant_id LEFT JOIN volunteer v ON v.id=f.volunteer_id WHERE (r.name LIKE ? OR f.food_type LIKE ?)"
    args = [f"%{term}%"] * 2
    if st: sql += " AND f.status=?"; args.append(st)
    return admin_page("donations", rows=q(sql + " ORDER BY f.id DESC", args), term=term, st=st)

@app.route("/admin/donations/<int:i>", methods=["GET", "POST"])
@needs("admin")
def admin_donation(i):
    f = q("SELECT f.*, r.name rname, r.kind FROM food_donation f JOIN restaurant_hotel r ON r.id=f.restaurant_id WHERE f.id=?", (i,), one=True)
    if not f: abort(404)
    if request.method == "POST":
        a = request.form.get("action")
        if a == "verify": run("UPDATE food_donation SET status='Verified' WHERE id=?", (i,))
        elif a == "reject": run("UPDATE food_donation SET status='Rejected' WHERE id=?", (i,))
        elif a == "assign" and request.form.get("volunteer_id"):
            run("UPDATE food_donation SET volunteer_id=?, distribution_location=?, status='Volunteer Assigned', task_accepted=0 WHERE id=?",
                (request.form["volunteer_id"], request.form.get("location", "").strip(), i))
        elif a == "status" and request.form.get("status") in STATUSES:
            run("UPDATE food_donation SET status=? WHERE id=?", (request.form["status"], i))
        else: flash("Please choose a volunteer first.", "error"); return redirect(request.url)
        flash("Donation updated.", "ok"); return redirect(request.url)
    return admin_page("donation", f=f, vols=q("SELECT * FROM volunteer ORDER BY name"))

@app.route("/admin/restaurants", methods=["GET", "POST"])
@needs("admin")
def admin_restaurants():
    if request.method == "POST":
        d = fields("name", "kind", "contact_person", "phone", "email", "address", "password"); d["email"] = d["email"].lower()
        if not show(check(d)):
            try:
                run("INSERT INTO restaurant_hotel(name,kind,contact_person,phone,email,address,password_hash) VALUES(?,?,?,?,?,?,?)",
                    (d["name"], d["kind"], d["contact_person"], d["phone"], d["email"], d["address"], generate_password_hash(d["password"])))
                flash("Restaurant/Hotel added. Share the login details with them.", "ok"); return redirect(request.url)
            except sqlite3.IntegrityError: flash("This email is already registered.", "error")
    rows = q("SELECT r.*, (SELECT COUNT(*) FROM food_donation WHERE restaurant_id=r.id) n FROM restaurant_hotel r ORDER BY r.id DESC")
    return admin_page("restaurants", rows=rows, f=request.form)

@app.route("/admin/volunteers")
@needs("admin")
def admin_volunteers():
    rows = q("""SELECT v.*, (SELECT COUNT(*) FROM food_donation WHERE volunteer_id=v.id) total,
                (SELECT COUNT(*) FROM food_donation WHERE volunteer_id=v.id AND status='Distributed') done FROM volunteer v ORDER BY v.id DESC""")
    return admin_page("volunteers", rows=rows)

def bene_fields():
    d = fields("name", "phone", "address", "family_members", "pref_location", "pref_time", "reg_date", "status")
    errs = check(d, optional=("phone",))
    if d["family_members"] and not (d["family_members"].isdigit() and int(d["family_members"]) > 0): errs.append("Family members must be a positive number.")
    return d, errs

@app.route("/admin/beneficiaries", methods=["GET", "POST"])
@needs("admin")
def admin_beneficiaries():
    if request.method == "POST":
        if request.form.get("action") == "status":
            run("UPDATE beneficiary SET status=? WHERE id=?", (request.form["status"], request.form["id"])); flash("Status updated.", "ok")
        else:
            d, errs = bene_fields()
            if not show(errs):
                i = run("INSERT INTO beneficiary(name,phone,address,family_members,pref_location,pref_time,reg_date,status) VALUES(?,?,?,?,?,?,?,?)",
                        (d["name"], d["phone"], d["address"], int(d["family_members"]), d["pref_location"], d["pref_time"], d["reg_date"], d["status"]))
                run("UPDATE beneficiary SET code=? WHERE id=?", (f"BEN-{i:04d}", i)); flash("Beneficiary added.", "ok")
        return redirect(url_for("admin_beneficiaries"))
    term = request.args.get("q", "").strip()
    rows = q("SELECT * FROM beneficiary WHERE name LIKE ? OR code LIKE ? OR address LIKE ? ORDER BY id DESC", [f"%{term}%"] * 3)
    return admin_page("beneficiaries", rows=rows, term=term, today=date.today().isoformat(), b=None)

@app.route("/admin/beneficiaries/<int:i>", methods=["GET", "POST"])
@needs("admin")
def admin_beneficiary(i):
    b = q("SELECT * FROM beneficiary WHERE id=?", (i,), one=True)
    if not b: abort(404)
    if request.method == "POST":
        d, errs = bene_fields()
        if not show(errs):
            run("UPDATE beneficiary SET name=?,phone=?,address=?,family_members=?,pref_location=?,pref_time=?,reg_date=?,status=? WHERE id=?",
                (d["name"], d["phone"], d["address"], int(d["family_members"]), d["pref_location"], d["pref_time"], d["reg_date"], d["status"], i))
            flash("Beneficiary updated.", "ok"); return redirect(url_for("admin_beneficiaries"))
    return admin_page("beneficiary", b=b)

@app.route("/admin/distributions", methods=["GET", "POST"])
@needs("admin")
def admin_distributions():
    if request.method == "POST":
        d = fields("donation_id", "beneficiary_id", "quantity", "location", "dist_date", "dist_time", "status")
        if not show(check(d)):
            don = q("SELECT volunteer_id FROM food_donation WHERE id=?", (d["donation_id"],), one=True)
            run("INSERT INTO distribution(donation_id,volunteer_id,beneficiary_id,quantity,location,dist_date,dist_time,status) VALUES(?,?,?,?,?,?,?,?)",
                (d["donation_id"], don["volunteer_id"], d["beneficiary_id"], d["quantity"], d["location"], d["dist_date"], d["dist_time"], d["status"]))
            flash("Distribution record saved.", "ok"); return redirect(request.url)
    rows = q("""SELECT d.*, f.food_type, r.name rname, v.name vname, b.name bname, b.code FROM distribution d
                JOIN food_donation f ON f.id=d.donation_id JOIN restaurant_hotel r ON r.id=f.restaurant_id
                LEFT JOIN volunteer v ON v.id=d.volunteer_id JOIN beneficiary b ON b.id=d.beneficiary_id ORDER BY d.id DESC""")
    dons = q("SELECT f.id, f.food_type, r.name rname FROM food_donation f JOIN restaurant_hotel r ON r.id=f.restaurant_id WHERE f.status IN ('Volunteer Assigned','Food Collected','Distributed')")
    return admin_page("distributions", rows=rows, dons=dons, bens=q("SELECT * FROM beneficiary WHERE status='Active'"), today=date.today().isoformat())

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
