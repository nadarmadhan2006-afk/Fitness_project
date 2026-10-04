"""Smart Fitness & Nutrition Tracker - Flask + SQLite (run: python app.py)"""
import base64, csv, io, json, os, sqlite3, urllib.error, urllib.request
from datetime import date, datetime
from functools import wraps
from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
DB = "fitness.db"
UPLOADS = os.path.join(app.root_path, "static", "uploads")

# Nutrition per 100 g: (calories, protein, carbs, fats)
FOODS = {
    "Rice (cooked)": (130, 2.7, 28, 0.3), "Chapati": (297, 9, 46, 7),
    "Dal (cooked)": (116, 9, 20, 0.4), "Paneer": (265, 18, 1.2, 20),
    "Egg (boiled)": (155, 13, 1.1, 11), "Chicken breast (cooked)": (165, 31, 0, 3.6),
    "Fish (cooked)": (206, 22, 0, 12), "Milk": (60, 3.2, 5, 3.3),
    "Curd": (61, 3.5, 4.7, 3.3), "Banana": (89, 1.1, 23, 0.3),
    "Apple": (52, 0.3, 14, 0.2), "Oats (dry)": (389, 17, 66, 7),
    "Bread (white)": (265, 9, 49, 3.2), "Peanut butter": (588, 25, 20, 50),
    "Almonds": (579, 21, 22, 50), "Potato (boiled)": (87, 1.9, 20, 0.1),
    "Idli": (130, 4, 27, 0.4), "Dosa": (168, 4, 29, 3.7),
    "Whey protein": (400, 80, 8, 6), "Chickpeas (cooked)": (164, 8.9, 27, 2.6),
    "Poha": (130, 2.6, 25, 2.2), "Samosa": (262, 3.5, 24, 17),
}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT);
    CREATE TABLE IF NOT EXISTS workouts(id INTEGER PRIMARY KEY, user_id INT, day TEXT, exercise TEXT, sets INT, reps INT, weight REAL);
    CREATE TABLE IF NOT EXISTS body(id INTEGER PRIMARY KEY, user_id INT, day TEXT, weight REAL, height REAL, age INT, sex TEXT, goal TEXT);
    CREATE TABLE IF NOT EXISTS meals(id INTEGER PRIMARY KEY, user_id INT, day TEXT, food TEXT, grams REAL, cal REAL, protein REAL, carbs REAL, fats REAL, photo TEXT);
    CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, user_id INT, label TEXT, time TEXT, kind TEXT);
    """)
    con.commit(); con.close()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session: return redirect(url_for("login"))
        return f(*a, **k)
    return w

def today(): return date.today().isoformat()

# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            db().execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                (request.form["name"], request.form["email"].lower(),
                 generate_password_hash(request.form["password"])))
            db().commit()
            flash("Account created. Log in to continue.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That email is already registered. Log in instead.")
    return render_template("auth.html", mode="register")

@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE email=?", (request.form["email"].lower(),)).fetchone()
        if u and check_password_hash(u["password"], request.form["password"]):
            session["uid"], session["name"] = u["id"], u["name"]
            return redirect(url_for("dashboard"))
        flash("Wrong email or password. Check both and try again.")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- body metrics & diet plan ----------
def plan_for(b):
    if not b: return None
    h = b["height"] / 100
    bmi = b["weight"] / (h * h)
    cat = ("Underweight" if bmi < 18.5 else "Normal" if bmi < 25 else
           "Overweight" if bmi < 30 else "Obese")
    bmr = 10 * b["weight"] + 6.25 * b["height"] - 5 * b["age"] + (5 if b["sex"] == "Male" else -161)
    tdee = bmr * 1.55  # moderately active
    cal = tdee + 300 if b["goal"] == "Bulk" else tdee - 400
    protein = 2.0 * b["weight"]
    fats = cal * 0.25 / 9
    carbs = (cal - protein * 4 - fats * 9) / 4
    return dict(bmi=round(bmi, 1), cat=cat, cal=round(cal), protein=round(protein),
                carbs=round(carbs), fats=round(fats), goal=b["goal"])

def latest_body(uid):
    return db().execute("SELECT * FROM body WHERE user_id=? ORDER BY day DESC, id DESC LIMIT 1", (uid,)).fetchone()

@app.route("/body", methods=["GET", "POST"])
@login_required
def body():
    uid = session["uid"]
    if request.method == "POST":
        f = request.form
        db().execute("INSERT INTO body(user_id,day,weight,height,age,sex,goal) VALUES(?,?,?,?,?,?,?)",
            (uid, today(), float(f["weight"]), float(f["height"]), int(f["age"]), f["sex"], f["goal"]))
        db().commit(); flash("Body metrics saved."); return redirect(url_for("body"))
    rows = db().execute("SELECT * FROM body WHERE user_id=? ORDER BY day DESC, id DESC", (uid,)).fetchall()
    return render_template("body.html", rows=rows, plan=plan_for(latest_body(uid)), last=latest_body(uid))

# ---------- workouts & progressive overload ----------
def overload_tip(uid, exercise):
    r = db().execute("SELECT * FROM workouts WHERE user_id=? AND lower(exercise)=lower(?) ORDER BY day DESC, id DESC LIMIT 1",
                     (uid, exercise)).fetchone()
    if not r: return None
    if r["reps"] >= 10:
        return f"Last time: {r['sets']}x{r['reps']} at {r['weight']} kg. You hit 10+ reps, so try {r['weight'] + 2.5} kg."
    return f"Last time: {r['sets']}x{r['reps']} at {r['weight']} kg. Keep {r['weight']} kg and aim for {r['reps'] + 1} reps."

@app.route("/workouts", methods=["GET", "POST"])
@login_required
def workouts():
    uid = session["uid"]
    if request.method == "POST":
        f = request.form
        db().execute("INSERT INTO workouts(user_id,day,exercise,sets,reps,weight) VALUES(?,?,?,?,?,?)",
            (uid, today(), f["exercise"].strip().title(), int(f["sets"]), int(f["reps"]), float(f["weight"])))
        db().commit(); flash("Workout logged."); return redirect(url_for("workouts"))
    rows = db().execute("SELECT * FROM workouts WHERE user_id=? ORDER BY day DESC, id DESC LIMIT 50", (uid,)).fetchall()
    ex = db().execute("SELECT DISTINCT exercise FROM workouts WHERE user_id=?", (uid,)).fetchall()
    tips = {e["exercise"]: overload_tip(uid, e["exercise"]) for e in ex}
    return render_template("workouts.html", rows=rows, tips=tips)

# ---------- food / macros (AI scan) ----------
PROMPT = (
    "You are a nutrition assistant. The image shows a meal or a nutrition label. "
    "List every food item you can see (Indian dishes are common) and estimate each portion in grams. "
    "Return ONLY a JSON array with no other text. Each element must be: "
    '{"name": str, "grams": number, "kcal_per_100g": number, "protein_per_100g": number, '
    '"carbs_per_100g": number, "fats_per_100g": number}. '
    "If the image is a nutrition label, return one item using the label values converted to per 100 g, "
    "and grams equal to the serving size. If there is no food, return []."
)

def guess_food(filename):
    """Offline fallback: matches a food name inside the photo's filename."""
    name = filename.lower()
    for f in FOODS:
        if f.split(" (")[0].lower() in name: return f
    return None

def find_key():
    """Key from env var GEMINI_API_KEY, or from gemini_key.txt (also gemini_key.txt.txt) next to app.py."""
    key = os.environ.get("GEMINI_API_KEY")
    if key: return key.strip()
    for name in ("gemini_key.txt", "gemini_key.txt.txt"):
        try:
            k = open(os.path.join(app.root_path, name), encoding="utf-8-sig").read().strip().strip('"')
            if k: return k
        except OSError:
            pass
    return None

_MODEL = {}
def pick_model(key):
    """Asks Google which models this key can use and picks the newest stable Flash model."""
    if "name" in _MODEL: return _MODEL["name"]
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                 headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        models = json.loads(r.read()).get("models", [])
    ok = [m["name"].split("/")[-1] for m in models
          if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
          and not any(x in m["name"] for x in ("image", "live", "tts", "audio", "lite", "thinking", "native", "robotics"))]
    stable = sorted(n for n in ok if not any(x in n for x in ("preview", "exp", "latest")))
    chosen = (stable or sorted(ok) or ["gemini-2.5-flash"])[-1]
    _MODEL["name"] = chosen
    print(">>> Using Gemini model:", chosen)
    return chosen

def ai_scan(path, original_name):
    """Returns (items, message). Uses the free Google Gemini API to read the photo."""
    key = find_key()
    if not key:
        f = guess_food(original_name)
        if not f: return [], "AI key not set and the filename has no food name. Add the food manually below."
        c, p, cb, ft = FOODS[f]
        return [dict(name=f, grams=100, kcal=c, protein=p, carbs=cb, fats=ft)], \
            "AI key not set, so the filename was used to guess the food. Set GEMINI_API_KEY for real AI scanning."
    try:
        raw = open(path, "rb").read()
        try:  # shrink big phone photos
            from PIL import Image
            img = Image.open(path).convert("RGB"); img.thumbnail((1280, 1280))
            buf = io.BytesIO(); img.save(buf, "JPEG", quality=85); raw = buf.getvalue()
        except Exception:
            pass
        body = json.dumps({
            "contents": [{"parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(raw).decode()}},
                {"text": PROMPT}]}],
            "generationConfig": {"responseMimeType": "application/json"}}).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{pick_model(key)}:generateContent",
            data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text[text.index("["): text.rindex("]") + 1])
        items = [dict(name=str(d["name"]), grams=float(d["grams"]), kcal=float(d["kcal_per_100g"]),
                      protein=float(d["protein_per_100g"]), carbs=float(d["carbs_per_100g"]),
                      fats=float(d["fats_per_100g"])) for d in data]
        return items, "No food found in the photo." if not items else ""
    except urllib.error.HTTPError as e:
        why = {429: "free limit reached, wait a minute and retry", 400: "bad request or invalid API key",
               403: "API key not allowed, create a new key"}.get(e.code, f"error {e.code}")
        _MODEL.clear()
        return [], f"AI scan failed: {why}. You can add the food manually below."
    except Exception as e:
        return [], f"AI scan failed ({type(e).__name__}). Check internet and try again, or add the food manually."

def add_meal(uid, name, grams, per100, photo):
    m = grams / 100
    db().execute("INSERT INTO meals(user_id,day,food,grams,cal,protein,carbs,fats,photo) VALUES(?,?,?,?,?,?,?,?,?)",
                 (uid, today(), name, grams, *[round(v * m, 1) for v in per100], photo))

@app.route("/food", methods=["GET", "POST"])
@login_required
def food():
    uid = session["uid"]
    f = request.form
    if request.method == "POST":
        action = f.get("action")
        if action == "scan":
            photo = request.files["photo"]
            saved = f"{uid}_{datetime.now():%Y%m%d%H%M%S}_{secure_filename(photo.filename)}"
            path = os.path.join(UPLOADS, saved); photo.save(path)
            items, msg = ai_scan(path, photo.filename)
            if items:  # add everything automatically; the user can fix grams in "Today's meals"
                for it in items:
                    add_meal(uid, it["name"], it["grams"], (it["kcal"], it["protein"], it["carbs"], it["fats"]), saved)
                db().commit()
                names = ", ".join(f"{it['name']} ({it['grams']:.0f} g)" for it in items)
                flash(f"Added automatically: {names}. Wrong portion? Change the grams below.")
            else:
                flash(msg)
            return redirect(url_for("food"))
        if action == "add_scan":
            names, grams = f.getlist("name"), f.getlist("grams")
            k, p, c, fa = (f.getlist(x) for x in ("k", "p", "c", "fa"))
            for i in map(int, f.getlist("keep")):
                add_meal(uid, names[i], float(grams[i]), (float(k[i]), float(p[i]), float(c[i]), float(fa[i])), f.get("saved"))
            db().commit(); flash("Meal added."); return redirect(url_for("food"))
        if action == "add":
            add_meal(uid, f["food"], float(f["grams"]), FOODS[f["food"]], None)
            db().commit(); flash("Meal added."); return redirect(url_for("food"))
    return render_template("food.html", foods=FOODS, items=None, saved=None, **meal_data(uid))

def meal_data(uid):
    meals = db().execute("SELECT * FROM meals WHERE user_id=? AND day=? ORDER BY id", (uid, today())).fetchall()
    tot = {k: round(sum(m[k] for m in meals), 1) for k in ("cal", "protein", "carbs", "fats")}
    return dict(meals=meals, tot=tot)

@app.route("/meal/delete/<int:mid>")
@login_required
def meal_delete(mid):
    db().execute("DELETE FROM meals WHERE id=? AND user_id=?", (mid, session["uid"])); db().commit()
    return redirect(url_for("food"))

@app.route("/meal/edit/<int:mid>", methods=["POST"])
@login_required
def meal_edit(mid):
    """Manual correction: change the quantity of a logged item."""
    m = db().execute("SELECT * FROM meals WHERE id=? AND user_id=?", (mid, session["uid"])).fetchone()
    if m and m["grams"]:
        g_ = float(request.form["grams"]); r = g_ / m["grams"]
        db().execute("UPDATE meals SET grams=?,cal=?,protein=?,carbs=?,fats=? WHERE id=?",
                     (g_, round(m["cal"] * r, 1), round(m["protein"] * r, 1), round(m["carbs"] * r, 1), round(m["fats"] * r, 1), mid))
        db().commit()
    return redirect(url_for("food"))

# ---------- reminders ----------
@app.route("/reminders", methods=["GET", "POST"])
@login_required
def reminders():
    uid = session["uid"]
    if request.method == "POST":
        db().execute("INSERT INTO reminders(user_id,label,time,kind) VALUES(?,?,?,?)",
                     (uid, request.form["label"], request.form["time"], request.form["kind"]))
        db().commit(); return redirect(url_for("reminders"))
    rows = db().execute("SELECT * FROM reminders WHERE user_id=? ORDER BY time", (uid,)).fetchall()
    return render_template("reminders.html", rows=rows)

@app.route("/reminder/delete/<int:rid>")
@login_required
def reminder_delete(rid):
    db().execute("DELETE FROM reminders WHERE id=? AND user_id=?", (rid, session["uid"])); db().commit()
    return redirect(url_for("reminders"))

# ---------- dashboard & export ----------
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["uid"]
    wt = db().execute("SELECT day, weight FROM body WHERE user_id=? ORDER BY day, id", (uid,)).fetchall()
    cal = db().execute("SELECT day, SUM(cal) c FROM meals WHERE user_id=? GROUP BY day ORDER BY day DESC LIMIT 7", (uid,)).fetchall()[::-1]
    vol = db().execute("SELECT day, SUM(sets*reps*weight) v FROM workouts WHERE user_id=? GROUP BY day ORDER BY day DESC LIMIT 7", (uid,)).fetchall()[::-1]
    plan = plan_for(latest_body(uid))
    now = datetime.now().strftime("%H:%M")
    rem = db().execute("SELECT * FROM reminders WHERE user_id=? AND time>=? ORDER BY time", (uid, now)).fetchall()
    insights = []
    m = meal_data(uid)["tot"]
    if plan:
        diff = plan["cal"] - m["cal"]
        insights.append(f"You have eaten {m['cal']:.0f} of {plan['cal']} kcal today. " +
                        (f"{diff:.0f} kcal left." if diff > 0 else "You are over your target."))
        if m["protein"] < plan["protein"] * 0.6 and m["cal"] > 0:
            insights.append(f"Protein is {m['protein']:.0f} g of {plan['protein']} g. Add a protein-rich food.")
    else:
        insights.append("Add your body metrics to get a personal calorie and macro plan.")
    return render_template("dashboard.html", plan=plan, today_tot=m, rem=rem, insights=insights,
        wt=[[r["day"], r["weight"]] for r in wt], cal=[[r["day"], round(r["c"])] for r in cal],
        vol=[[r["day"], round(r["v"])] for r in vol])

@app.route("/export")
@login_required
def export():
    uid = session["uid"]
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["WORKOUTS"]); w.writerow(["date", "exercise", "sets", "reps", "weight_kg"])
    for r in db().execute("SELECT * FROM workouts WHERE user_id=? ORDER BY day", (uid,)): w.writerow([r["day"], r["exercise"], r["sets"], r["reps"], r["weight"]])
    w.writerow([]); w.writerow(["MEALS"]); w.writerow(["date", "food", "grams", "kcal", "protein", "carbs", "fats"])
    for r in db().execute("SELECT * FROM meals WHERE user_id=? ORDER BY day", (uid,)): w.writerow([r["day"], r["food"], r["grams"], r["cal"], r["protein"], r["carbs"], r["fats"]])
    w.writerow([]); w.writerow(["BODY"]); w.writerow(["date", "weight_kg", "height_cm"])
    for r in db().execute("SELECT * FROM body WHERE user_id=? ORDER BY day", (uid,)): w.writerow([r["day"], r["weight"], r["height"]])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=fitness_report.csv"})

if __name__ == "__main__":
    init_db(); os.makedirs(UPLOADS, exist_ok=True)
    print("\n>>> Running from:", app.root_path)
    print(">>> Gemini key found:", "YES" if find_key() else "NO - put it in gemini_key.txt in the folder above\n")
    app.run(debug=True)
