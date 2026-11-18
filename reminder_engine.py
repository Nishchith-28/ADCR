"""
reminder_engine.py — the "automatic" part of the automatic drug reminder.

A daemon thread ticks once a second, lined up with the clock, and:
  1. Creates a reminder row the moment a prescribed dose time arrives
     (so a 14:30 dose is created at 14:30:00, not up to 15 seconds later).
  2. Looks at reminders that have been sitting un-answered for longer than
     ESCALATION_MINUTES and escalates them: marks them 'missed' and files
     an alert for every guardian linked to that patient.

Nothing here talks to the network — it only touches the local SQLite file,
so it needs no API keys and works completely offline.
"""
import threading
import time
import json
from datetime import datetime, timedelta

import db
import push

TICK_SECONDS = 1           # how often the engine checks the clock
ESCALATION_MINUTES = 20    # no response this long after dose time -> alert guardian
SNOOZE_MINUTES = 10        # "remind me later" delay
CATCHUP_MINUTES = 20       # if the server was off, don't invent reminders for doses
                           # older than this (they'd instantly alert the guardian)


# ---------------------------------------------------------------- time helpers

def parse_time(value):
    """'8:00', '08:00', '8:00 PM' or '8:00pm' -> '08:00' / '20:00'. None if invalid."""
    text = str(value).strip().upper()
    for fmt in ("%H:%M", "%I:%M%p", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def prescription_times(presc):
    """The prescription's dose times as a sorted list of clean 'HH:MM' strings."""
    try:
        raw = json.loads(presc["times"])
    except Exception:
        return []
    clean = {parse_time(t) for t in raw}
    clean.discard(None)
    return sorted(clean)


def course_end_date(start_date, duration_days):
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        return (start + timedelta(days=int(duration_days) - 1)).strftime("%Y-%m-%d")
    except Exception:
        return start_date


# ---------------------------------------------------------------- engine steps

def generate_due_reminders():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    for presc in db.get_active_prescriptions():
        if not (presc["start_date"] <= today <= course_end_date(presc["start_date"], presc["duration_days"])):
            continue

        for hm in prescription_times(presc):
            dose_dt = datetime.strptime(f"{today} {hm}", "%Y-%m-%d %H:%M")
            if dose_dt > now:
                continue                                    # not due yet
            if now - dose_dt > timedelta(minutes=CATCHUP_MINUTES):
                continue                                    # too old to remind about now
            dose_time = f"{today} {hm}"
            if not db.reminder_exists(presc["id"], dose_time):
                db.create_reminder(
                    presc["id"], presc["patient_id"], presc["medicine_name"],
                    presc["dosage"], dose_time,
                )
                reminder = db.get_reminder_by_dose(presc["id"], dose_time)
                if reminder:
                    push.notify_reminder(reminder, SNOOZE_MINUTES)   # to the patient's phone


_snooze_notified = set()


def notify_snoozed_due():
    """"Remind me later" ran out: send the reminder to the patient's phone again."""
    for rem in db.get_snoozed_due_reminders(db.now_str()):
        key = (rem["id"], rem["snooze_until"])
        if key in _snooze_notified:
            continue
        _snooze_notified.add(key)
        push.notify_reminder(rem, SNOOZE_MINUTES)


def escalate_stale_reminders():
    cutoff = (datetime.now() - timedelta(minutes=ESCALATION_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    for rem in db.get_stale_pending_reminders(cutoff):
        db.set_reminder_status(rem["id"], "missed")
        if not db.alert_already_sent_for_reminder(rem["id"]):
            _alert_guardians(rem, reason="no_response")


def _alert_guardians(reminder_row, reason):
    guardians = db.get_guardians_for_patient(reminder_row["patient_id"])
    patient = db.get_user_by_id(reminder_row["patient_id"])
    patient_name = patient["name"] if patient else "Patient"
    if reason == "no_response":
        message = (f"{patient_name} did not respond about {reminder_row['medicine_name']} "
                   f"(scheduled {reminder_row['dose_time']}). It has been marked missed.")
    else:
        message = (f"{patient_name} marked {reminder_row['medicine_name']} "
                   f"(scheduled {reminder_row['dose_time']}) as NOT TAKEN.")
    for g in guardians:
        db.create_guardian_alert(g["id"], reminder_row["patient_id"], reminder_row["id"], message)
        push.notify_alert(g["id"], reminder_row["id"], message)       # to the guardian's phone


def alert_guardians_not_taken(reminder_row):
    """Called immediately when a patient explicitly taps 'Not taken'."""
    _alert_guardians(reminder_row, reason="not_taken")


def _loop():
    while True:
        try:
            generate_due_reminders()
            notify_snoozed_due()
            escalate_stale_reminders()
        except Exception as e:
            print(f"[reminder_engine] error: {e}")
        # Sleep until just after the next tick boundary so we line up with the clock.
        time.sleep(max(0.05, TICK_SECONDS - (time.time() % TICK_SECONDS) + 0.01))


def start():
    db.init_db()
    t = threading.Thread(target=_loop, daemon=True, name="reminder-engine")
    t.start()
    return t
