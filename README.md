# Dosely — Automatic Drug Reminder

A doctor writes a prescription once. Dosely reminds the patient at every
dose, right in the browser, and if a dose is skipped it automatically
alerts a linked guardian — no one has to chase anyone.

Built with **Python (Flask) + SQLite only** — nothing else to install,
nothing to sign up for, and no API keys. It runs entirely on your own
machine.

---

## 1. Setup

**Easiest way (Windows):** double-click **`Start Dosely.bat`**. The first time
it installs what Dosely needs, then it starts the server and opens your
browser on Dosely, so you never have to type an address (see section 14).

Or by hand:

```bash
cd drug_reminder
pip install -r requirements.txt      # installs Flask + cryptography
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

The first run creates `drugreminder.db` (SQLite) automatically — nothing
to configure.

> Tested on Python 3.12. Any Python 3.9+ should work.

## 2. Try it in under two minutes

1. **Register as a doctor** (e.g. `doc@example.com`).
2. **Register as a patient** in a second browser tab / private window
   (e.g. `patient@example.com`).
3. **Register as a guardian** in a third tab (e.g. `guardian@example.com`).
4. As the doctor, go to **New prescription**, enter the patient's email,
   a medicine name, and a dose time set to a minute or two from now
   (24-hour clock, e.g. `14:32`). Save it.
5. As the patient, open the **Guardians** page and link the guardian's
   email — this is what makes escalation possible.
6. Wait for the dose time. Within a second or two of it arriving, a reminder
   card pops up on the patient dashboard (and, if you allow the browser
   permission prompt, a real desktop notification too) with three
   buttons: **Taken**, **Remind me later**, **Not taken**.
7. Click **Not taken** — switch to the guardian tab and within ~20
   seconds an alert appears there automatically.

If a reminder is left completely unanswered for 20 minutes, the engine
escalates it to the guardian on its own — you don't need to click
anything to see that path, it just needs 20 real minutes to pass (see
"Tuning" below if you want to test that faster).

## 3. How the three roles work

**Doctor**
- Dashboard listing patients and prescriptions written.
- "New prescription" form: patient's email, medicine, dosage, one or
  more dose times, start date, course length in days, optional
  instructions, and an optional scanned copy (PDF/PNG/JPG).
- Can stop a prescription early (stops future reminders for it).

**Patient**
- Dashboard with a visual timeline of today's doses (taken / due /
  missed / upcoming) and live reminder cards as doses come due.
- "My prescriptions" — full history with dosage, schedule, instructions
  and the attached file if the doctor uploaded one.
- "Guardians" — link the email of whoever should be alerted if a dose
  is missed.

**Guardian**
- Dashboard showing linked patients and a live feed of alerts, triggered
  either by the patient explicitly tapping "Not taken," or by a
  reminder timing out unanswered.

## 4. How the "automatic" part works

A background thread (`reminder_engine.py`) ticks every second, lined up with the clock, and:

1. Looks at every active prescription and, for each dose time that has
   arrived today, creates a reminder if one doesn't already exist.
2. Looks at reminders that have been pending for more than 20 minutes
   with no response, marks them **missed**, and files an alert for
   every guardian linked to that patient.

The patient's browser polls `/api/reminders/due` every 10 seconds (and also checks at the exact moment each upcoming dose is due) and
the guardian's browser polls `/api/alerts/unread` every 20 seconds —
that's what makes the reminder/alert cards appear without refreshing
the page. Both also trigger a real browser `Notification` if the user
has granted permission.

**Tuning** (top of `reminder_engine.py`):
```python
TICK_SECONDS = 1           # how often the engine checks the clock
ESCALATION_MINUTES = 20    # unanswered-too-long -> alert guardian
SNOOZE_MINUTES = 10        # "remind me later" delay
```

## 5. Project layout

```
drug_reminder/
  app.py                 # Flask routes (auth, doctor, patient, guardian, JSON API)
  db.py                  # SQLite schema + all queries (plain sqlite3, no ORM)
  reminder_engine.py      # background thread: generates + escalates reminders
  requirements.txt
  templates/              # Jinja2 pages
  static/css/style.css    # design system
  static/js/main.js       # polling + notification + reminder/alert cards
  static/manifest.json    # lets the site be installed on a phone's home screen
  static/sw.js            # service worker (phone notifications + installability)
  static/icons/           # app icons
  static/uploads/prescriptions/  # uploaded prescription files land here
```

## 6. Known limitations (by design, so it runs with zero setup)

> Update: notifications that reach the device even when the page is closed are now
> supported. See section 13. The points below describe the page-only behaviour.

- **Browser notifications only fire while a dashboard tab is open.**
  There's no push-when-the-app-is-closed and no real SMS — that needs a
  paid provider (e.g. Twilio for SMS, or a service-worker + VAPID setup
  for background push) and API keys nobody can hand you for free. The
  in-app + browser-notification approach here needs neither.
- **Single-process dev server.** `app.run(...)` is Flask's built-in
  server — fine for a demo or a class project, not for production.
  For real deployment, run it behind something like gunicorn + nginx.
- **No email verification / password reset** — kept out to keep the
  scope focused on the reminder flow itself.
- The demo `secret_key` in `app.py` is not secure — change it before
  putting this anywhere public.

## 7. If you want to add real SMS later

Swap the `_alert_guardians()` and reminder-creation points in
`reminder_engine.py` / `app.py` for a call to an SMS API (Twilio,
MSG91, etc.) using the `phone` field already stored on every user. The
data model doesn't need to change — you're just adding a network call
alongside the existing in-app alert.

## 8. Using Dosely on your phone

The site is phone-friendly (responsive layout, big tap targets) and can be
installed to your home screen like an app.

### Open it on your phone (same Wi-Fi)
1. Start the server on your computer: `python app.py` (it already listens
   on all network addresses).
2. Find your computer's local IP address. On Windows run `ipconfig` and look
   for "IPv4 Address" (for example `192.168.1.23`).
3. On your phone, connected to the **same Wi-Fi**, open Chrome and go to
   `http://192.168.1.23:5000` (use your own IP).
4. If it won't load: Windows usually shows a firewall prompt the first time,
   so choose **Allow** for private networks. Also make sure the phone isn't
   on a guest Wi-Fi network.

### Add it to your home screen
Chrome menu (three dots) > **Add to Home screen** / **Install app**.

### Getting real phone notifications
Phones only allow system notifications (and a full "install") on a secure
HTTPS address, and `http://192.168...` isn't one. Without HTTPS everything
else still works (reminder cards, the three buttons, guardian alerts); only
the pop-up system notification is missing. To get it, pick one:

- **Quick test on your own phone:** in Chrome on the phone open
  `chrome://flags`, search for "Insecure origins treated as secure",
  enable it, type your site address (e.g. `http://192.168.1.23:5000`) into
  the box, and tap Relaunch. Then open the site and tap **Turn on** in the
  banner on the dashboard.
- **A proper HTTPS link:** a tunnel tool such as Cloudflare Tunnel
  (`cloudflared tunnel --url http://localhost:5000`) or ngrok
  (`ngrok http 5000`) gives you a temporary `https://` address that opens on
  any phone. (Not tested here.)
- **Hosting it online:** deploy to a host that gives you HTTPS and keeps the
  app process running, because the reminder engine is a background thread
  inside the app.

### Limits to know about
- The computer running `python app.py` has to be switched on. If it's off,
  no reminders are generated.
- Reminders appear while the Dosely page or app is open, or was recently open
  in the background. Android can pause idle tabs, so the page re-checks
  immediately when you come back to it. Alerts that arrive with the app
  fully closed need server-sent push (Web Push) or a native app.

## 9. Android app (APK)

The `DoselyAndroid` project (a separate download) wraps this website in a
small Android app with native phone notifications that work over plain
`http://` on your Wi-Fi. See its README for how to build the APK in Android
Studio. Logins now last 30 days so the app doesn't keep asking you to sign in.

## 10. Exact dose times

- The server creates a reminder at the dose time itself (measured: about
  0.06 seconds after, e.g. 14:30:00).
- Dose times can be typed as `8:00`, `08:00` or `8:00 PM`; they're checked and
  stored as 24-hour `HH:MM`. Invalid times are rejected when the prescription
  is saved.
- If the server was off when a dose was due, it only creates reminders for
  doses from the last 20 minutes (`CATCHUP_MINUTES` in `reminder_engine.py`).
  Older ones are skipped, so a new prescription never instantly alerts the
  guardian about earlier times in the day.

## 11. Looking inside the database

Dosely uses **SQLite**: one file called `drugreminder.db`, created next to
`app.py` the first time you run the app. It lives on the computer running the
server. There is no cloud database and no account to set up.

- See everything in it: `python view_db.py`
- Or open the file in a free app such as **DB Browser for SQLite**
  (https://sqlitebrowser.org): File > Open Database > `drugreminder.db`.
- Or from a terminal (if you have the `sqlite3` tool): `sqlite3 drugreminder.db`
  then `.tables` and `SELECT * FROM users;`
- To start again with an empty database, stop the app and delete
  `drugreminder.db`; a fresh one is created on the next run.

Tables: `users`, `prescriptions`, `reminders`, `guardian_links`,
`guardian_alerts`, `push_subscriptions` (all defined at the top of `db.py`). Passwords are never
stored as typed, only as salted hashes.

## 12. Notification sound

The "ting" (`static/sounds/notify.mp3`) plays whenever a reminder or alert
card pops up on the page. Browsers only allow sound after the person has
tapped or clicked the page once, so if you open the dashboard and a reminder
is already waiting, it may stay silent until the first tap. A pop-up that the
phone's system shows while the page is closed uses the phone's own default
sound; web pages can't change that. The Android app (see the
`DoselyAndroid` project) plays the same sound on every notification, even
with the app closed.

## 13. Real notifications on the device (Web Push)

Reminders no longer depend on the Dosely page being open. At the dose time the
server sends the notification straight to the phone's system, so it appears
even when the page and the browser are closed. Tapping a button on the
notification answers the reminder without opening the page.

### How to set it up
1. `pip install -r requirements.txt` (adds the `cryptography` package) and run
   `python app.py`. The first run creates `vapid_private.pem`, the server's
   push identity. **Keep this file, don't share it, and don't delete it**: if
   it is lost every phone has to turn notifications on again.
2. **The phone must open Dosely over HTTPS** (phones refuse push
   notifications on plain `http://192.168...`). The easiest way for a demo is a
   free Cloudflare quick tunnel, which needs no account:
   - Install it (Windows): `winget install --id Cloudflare.cloudflared`
   - With `python app.py` running, open a second terminal and run:
     `cloudflared tunnel --url http://localhost:5000`
   - It prints an address like `https://something-random.trycloudflare.com`.
     (Not tested here, so if it gives trouble, see "Hosting" below.)
3. On the **phone**, open that `https://` address in Chrome and log in as the
   patient. Tap **Turn on** in the green/teal banner and allow notifications.
   The banner changes to "Notifications are on for this device".
4. Tap **Send test**. A notification should pop up on the phone. Do the same on
   the guardian's phone, logged in as the guardian.
5. Close the browser. Add a prescription with a dose time two minutes from now.
   The notification arrives at that time, with **Taken / Remind me later / Not
   taken** buttons (phones may show only the first two; tap the notification
   itself to see all three on the page).

### What happens
- Dose time: the patient's phone gets the reminder.
- "Remind me later": the phone gets it again after 10 minutes.
- "Not taken", or no answer for 20 minutes: the guardian's phone gets an alert.
- If the page is open and visible on the dashboard, no pop-up is shown: the page
  shows its own card (with the "ting" sound) so you don't get it twice.

### Things to know
- The computer running `python app.py` must be on and online. It sends the
  notifications (and the tunnel must be running if you use one).
- The tunnel address changes every time you restart it. A new address counts as
  a new website to the phone, so notifications must be turned on again, and
  anyone who has the address can open the login page. Fine for a demo; don't
  put real patient data on it.
- Android: if notifications are late or missing, set Chrome's battery usage to
  "Unrestricted". iPhone: Apple only allows this for sites added to the Home
  Screen (iOS 16.4 or newer); add Dosely to the Home Screen, then turn on
  notifications inside that app.
- The "ting" sound plays when the page is open. A pop-up that the phone shows
  itself uses the phone's own notification sound; web pages can't change that.
- Contact address: push services like to see a real address from the sender.
  Optionally set `DOSELY_VAPID_SUBJECT=mailto:you@yourdomain.com` before
  starting the app.

### Hosting instead of a tunnel
For something permanent, host the whole app on a server that gives you HTTPS
and stays switched on 24 hours a day (the reminder engine runs inside the
app). Hosts that put free apps to sleep will miss reminders.

### Files involved
`push.py` (key handling, encryption, sending), `static/sw.js` (receives the
push and shows the notification), `static/js/main.js` (banner + registering the
device), and the `push_subscriptions` table.

## 14. Opening Dosely with one double-click

- **`Start Dosely.bat`** (Windows): installs the requirements the first time,
  starts the server, and opens `Dosely.html`. Keep its window open while you
  use Dosely; closing it stops Dosely.
- **`Dosely.html`**: a launcher page. Open it (double-click) and it looks for
  the Dosely server, then takes you straight into the site the moment the
  server answers. If the server isn't running it says so and keeps waiting,
  so it opens by itself as soon as you start it.
  - A web page isn't allowed to start programs, so this file can't start the
    server on its own. That is what `Start Dosely.bat` (or `python app.py`)
    is for.
  - It looks at `http://127.0.0.1:5000` by default. "Using a different
    address?" saves another one in your browser, for example another
    computer on your Wi-Fi.
  - Once Dosely is open, the browser's address bar will show
    `127.0.0.1:5000`. That is where the server lives; you just never have to
    type it. Bookmark `Dosely.html` for one-click access.
- macOS / Linux: run `python3 app.py` in a terminal, then open `Dosely.html`.
