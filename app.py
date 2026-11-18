"""
app.py — Automatic Drug Reminder

Run with:
    pip install flask
    python app.py
Then open http://127.0.0.1:5000
"""
import os
import json
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    jsonify, abort, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import db
import push
import reminder_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads", "prescriptions")
ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-demo-key")
app.permanent_session_lifetime = timedelta(days=30)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB upload cap


# ---------------------------------------------------------------- helpers

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.get_user_by_id(uid)


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                flash("That page isn't available for your account type.", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


# ---------------------------------------------------------------- phone / PWA

@app.route("/sw.js")
def service_worker():
    # Must be served from the site root so it can control every page.
    resp = send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js",
                               mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ---------------------------------------------------------------- landing / auth

@app.route("/")
def index():
    user = current_user()
    if user:
        return redirect(url_for(f'{user["role"]}_dashboard'))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        phone = request.form.get("phone", "").strip()

        if not name or not email or not password or role not in ("doctor", "patient", "guardian"):
            flash("Please fill in every field.", "error")
            return render_template("register.html", form=request.form)

        if db.get_user_by_email(email):
            flash("An account with that email already exists — try logging in instead.", "error")
            return render_template("register.html", form=request.form)

        uid = db.create_user(name, email, generate_password_hash(password), role, phone)
        session["user_id"] = uid
        session.permanent = True
        flash(f"Welcome, {name}! Your {role} account is ready.", "success")
        return redirect(url_for(f"{role}_dashboard"))

    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Email or password is incorrect.", "error")
            return render_template("login.html", email=email)
        session["user_id"] = user["id"]
        session.permanent = True
        flash(f"Welcome back, {user['name']}.", "success")
        return redirect(url_for(f'{user["role"]}_dashboard'))
    return render_template("login.html", email="")


@app.route("/logout")
def logout():
    # This phone should stop receiving this person's notifications.
    endpoint = session.get("push_endpoint")
    if endpoint:
        db.delete_push_subscription(endpoint)
    session.clear()
    flash("You've been logged out.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- doctor

@app.route("/doctor/dashboard")
@login_required("doctor")
def doctor_dashboard():
    user = current_user()
    patients = db.get_patients_for_doctor(user["id"])
    prescriptions = db.get_prescriptions_by_doctor(user["id"])
    return render_template("doctor_dashboard.html", patients=patients, prescriptions=prescriptions)


@app.route("/doctor/prescribe", methods=["GET", "POST"])
@login_required("doctor")
def doctor_prescribe():
    user = current_user()
    if request.method == "POST":
        patient_email = request.form.get("patient_email", "").strip().lower()
        medicine_name = request.form.get("medicine_name", "").strip()
        dosage = request.form.get("dosage", "").strip()
        times_raw = request.form.get("times", "").strip()
        start_date = request.form.get("start_date", "").strip()
        duration_days = request.form.get("duration_days", "").strip()
        instructions = request.form.get("instructions", "").strip()

        patient = db.get_user_by_email(patient_email)
        errors = []
        if not patient or patient["role"] != "patient":
            errors.append("No patient account found with that email. Ask the patient to register first.")
        if not medicine_name:
            errors.append("Medicine name is required.")
        times_list, bad_times = [], []
        for raw in times_raw.split(","):
            raw = raw.strip()
            if not raw:
                continue
            hm = engine.parse_time(raw)
            if hm is None:
                bad_times.append(raw)
            elif hm not in times_list:
                times_list.append(hm)
        if bad_times:
            errors.append("These dose times aren't valid: " + ", ".join(bad_times) +
                          ". Use times like 08:00, 14:30 or 8:00 PM.")
        elif not times_list:
            errors.append("Add at least one dose time, e.g. 08:00, 14:00, 20:00.")
        if not start_date:
            errors.append("Start date is required.")
        try:
            duration_days = int(duration_days)
            if duration_days < 1:
                raise ValueError
        except ValueError:
            errors.append("Duration must be a whole number of days.")
            duration_days = None

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("prescription_form.html", form=request.form)

        file_path = None
        file = request.files.get("prescription_file")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Prescription file must be a PDF, PNG or JPG.", "error")
                return render_template("prescription_form.html", form=request.form)
            safe_name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            file.save(os.path.join(UPLOAD_DIR, safe_name))
            file_path = f"uploads/prescriptions/{safe_name}"

        db.create_prescription(
            user["id"], patient["id"], medicine_name, dosage, times_list,
            start_date, duration_days, instructions, file_path,
        )
        flash(f"Prescription for {patient['name']} saved and scheduled.", "success")
        return redirect(url_for("doctor_dashboard"))

    prefill = {"start_date": datetime.now().strftime("%Y-%m-%d")}
    return render_template("prescription_form.html", form=prefill)


@app.route("/doctor/patient/<int:patient_id>")
@login_required("doctor")
def doctor_view_patient(patient_id):
    user = current_user()
    patient = db.get_user_by_id(patient_id)
    if not patient or patient["role"] != "patient":
        abort(404)
    all_presc = db.get_prescriptions_by_doctor(user["id"])
    prescriptions = [p for p in all_presc if p["patient_id"] == patient_id]
    return render_template("doctor_patient_view.html", patient=patient, prescriptions=prescriptions)


@app.route("/doctor/prescription/<int:prescription_id>/stop", methods=["POST"])
@login_required("doctor")
def doctor_stop_prescription(prescription_id):
    user = current_user()
    db.stop_prescription(prescription_id, user["id"])
    flash("Prescription stopped. No further reminders will be sent for it.", "success")
    return redirect(request.referrer or url_for("doctor_dashboard"))


# ---------------------------------------------------------------- patient

@app.route("/patient/dashboard")
@login_required("patient")
def patient_dashboard():
    user = current_user()
    today = datetime.now().strftime("%Y-%m-%d")
    today_reminders = db.get_today_reminders_for_patient(user["id"], today)
    reminders_by_dose = {r["dose_time"]: r for r in today_reminders}

    prescriptions = db.get_prescriptions_for_patient(user["id"])
    guardians = db.get_guardians_for_patient(user["id"])
    active_count = sum(1 for p in prescriptions if p["status"] == "active")

    # Build today's dose timeline by merging each active prescription's
    # schedule with whatever reminder rows already exist for today.
    timeline = []
    for p in prescriptions:
        if p["status"] != "active":
            continue
        if not (p["start_date"] <= today <= engine.course_end_date(p["start_date"], p["duration_days"])):
            continue
        now_hm = datetime.now().strftime("%H:%M")
        for t in engine.prescription_times(p):
            dose_time = f"{today} {t}"
            rem = reminders_by_dose.get(dose_time)
            status = "past" if t < now_hm else "upcoming"
            if rem:
                status = "due" if rem["status"] == "pending" else rem["status"]
            timeline.append({
                "time": t, "medicine_name": p["medicine_name"], "status": status,
            })
    timeline.sort(key=lambda x: x["time"])

    return render_template(
        "patient_dashboard.html",
        timeline=timeline,
        prescriptions=prescriptions,
        guardians=guardians,
        active_count=active_count,
    )


@app.route("/patient/prescriptions")
@login_required("patient")
def patient_prescriptions():
    user = current_user()
    prescriptions = db.get_prescriptions_for_patient(user["id"])
    return render_template("prescription_viewer.html", prescriptions=prescriptions)


@app.route("/patient/guardians", methods=["GET", "POST"])
@login_required("patient")
def patient_guardians():
    user = current_user()
    if request.method == "POST":
        guardian_email = request.form.get("guardian_email", "").strip().lower()
        guardian = db.get_user_by_email(guardian_email)
        if not guardian or guardian["role"] != "guardian":
            flash("No guardian account found with that email. Ask them to register as a guardian first.", "error")
        elif guardian["id"] == user["id"]:
            flash("You can't link yourself as your own guardian.", "error")
        else:
            db.link_guardian(user["id"], guardian["id"])
            flash(f"{guardian['name']} will now be alerted if you miss a dose.", "success")
        return redirect(url_for("patient_guardians"))

    guardians = db.get_guardians_for_patient(user["id"])
    return render_template("patient_guardians.html", guardians=guardians)


# ---- patient-facing reminder API (polled by JS) ----

@app.route("/api/reminders/due")
@login_required("patient")
def api_reminders_due():
    user = current_user()
    rows = db.get_due_reminders_for_patient(user["id"])
    return jsonify([dict(r) for r in rows])


@app.route("/api/schedule")
@login_required("patient")
def api_schedule():
    """Upcoming doses for the next 3 days. The Android app turns these into
    exact phone alarms, so a notification fires at the dose time itself."""
    user = current_user()
    now = datetime.now()
    doses = []
    for p in db.get_prescriptions_for_patient(user["id"]):
        if p["status"] != "active":
            continue
        end_date = engine.course_end_date(p["start_date"], p["duration_days"])
        for offset in range(3):
            day = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            if not (p["start_date"] <= day <= end_date):
                continue
            for hm in engine.prescription_times(p):
                dose_dt = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
                if dose_dt > now:
                    doses.append({
                        "prescription_id": p["id"],
                        "medicine_name": p["medicine_name"],
                        "dosage": p["dosage"],
                        "dose_time": f"{day} {hm}",
                    })
    doses.sort(key=lambda d: d["dose_time"])
    return jsonify(doses)


@app.route("/api/reminders/<int:reminder_id>/respond", methods=["POST"])
@login_required("patient")
def api_reminder_respond(reminder_id):
    user = current_user()
    reminder = db.get_reminder(reminder_id)
    if not reminder or reminder["patient_id"] != user["id"]:
        abort(404)

    action = (request.get_json(silent=True) or {}).get("action")
    if action == "taken":
        db.set_reminder_status(reminder_id, "taken")
    elif action == "snooze":
        snooze_until = (datetime.now() + timedelta(minutes=engine.SNOOZE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        db.snooze_reminder(reminder_id, snooze_until)
    elif action == "missed":
        db.set_reminder_status(reminder_id, "missed")
        if not db.alert_already_sent_for_reminder(reminder_id):
            engine.alert_guardians_not_taken(reminder)
    else:
        return jsonify({"error": "unknown action"}), 400

    return jsonify({"ok": True, "status": action})


# ---------------------------------------------------------------- guardian

@app.route("/guardian/dashboard")
@login_required("guardian")
def guardian_dashboard():
    user = current_user()
    patients = db.get_patients_for_guardian(user["id"])
    alerts = db.get_alerts_for_guardian(user["id"])
    return render_template("guardian_dashboard.html", patients=patients, alerts=alerts)


@app.route("/api/alerts/unread")
@login_required("guardian")
def api_alerts_unread():
    user = current_user()
    rows = db.get_alerts_for_guardian(user["id"], unread_only=True)
    return jsonify([dict(r) for r in rows])


@app.route("/api/alerts/<int:alert_id>/read", methods=["POST"])
@login_required("guardian")
def api_alert_read(alert_id):
    user = current_user()
    db.mark_alert_read(alert_id, user["id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------- phone notifications (Web Push)

def _push_user_or_error():
    user = current_user()
    if user["role"] not in ("patient", "guardian"):
        return None, (jsonify({"error": "notifications are for patients and guardians"}), 403)
    return user, None


@app.route("/api/push/key")
@login_required()
def api_push_key():
    return jsonify({"publicKey": push.public_key()})


@app.route("/api/push/subscribe", methods=["POST"])
@login_required()
def api_push_subscribe():
    user, error = _push_user_or_error()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    keys = data.get("keys") or {}
    endpoint, p256dh, auth = data.get("endpoint", ""), keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth) or not push.endpoint_allowed(endpoint):
        return jsonify({"error": "invalid subscription"}), 400
    db.save_push_subscription(user["id"], endpoint, p256dh, auth)
    session["push_endpoint"] = endpoint
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required()
def api_push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    if endpoint:
        db.delete_push_subscription(endpoint)
    return jsonify({"ok": True})


@app.route("/api/push/test", methods=["POST"])
@login_required()
def api_push_test():
    user, error = _push_user_or_error()
    if error:
        return error
    devices = db.get_push_subscriptions(user["id"])
    if devices:
        push.notify_user(user["id"], {
            "kind": "test", "title": "Dosely test",
            "body": "Notifications are working on this device.",
            "tag": "dosely-test", "url": "/", "force": True,
        }, ttl=120)
    return jsonify({"sent": len(devices)})


# ---------------------------------------------------------------- entry point

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    push.start()
    engine.start()
    app.run(
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 5000)),
    debug=False,
    use_reloader=False
)
