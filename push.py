"""
push.py - real phone/desktop notifications (Web Push).

How it works, in plain words:
  * The browser on the patient's phone gives the page a private "mailbox
    address" (a push subscription) that belongs to Google/Mozilla/Apple.
  * The page sends that address to our server, which saves it.
  * When a dose is due, the server drops an encrypted message into that mailbox.
    The phone wakes the browser's service worker (static/sw.js) and shows the
    notification, even if the page and the browser are closed.

The server proves who it is with a key pair called VAPID. The private half is
created automatically the first time the app runs and is saved in
vapid_private.pem - keep that file; if it is lost every phone has to turn
notifications on again.

Needs only the `cryptography` package. Message encryption follows RFC 8291
(aes128gcm); the sender identity follows RFC 8292 (VAPID).
"""
import base64
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import db

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vapid_private.pem")

# Push services (Apple especially) want a real contact address here.
# Set it with:  set DOSELY_VAPID_SUBJECT=mailto:you@yourdomain.com
VAPID_SUBJECT = os.environ.get("DOSELY_VAPID_SUBJECT", "mailto:admin@example.com")

_key_lock = threading.Lock()
_private_key = None


# ---------------------------------------------------------------- small helpers

def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(public_key):
    return public_key.public_bytes(serialization.Encoding.X962,
                                   serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------- VAPID identity

def _get_private_key():
    """Loads the server's key, creating and saving it on the very first run."""
    global _private_key
    with _key_lock:
        if _private_key is None:
            if os.path.exists(KEY_FILE):
                with open(KEY_FILE, "rb") as f:
                    _private_key = serialization.load_pem_private_key(f.read(), password=None)
            else:
                _private_key = ec.generate_private_key(ec.SECP256R1())
                pem = _private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                with open(KEY_FILE, "wb") as f:
                    f.write(pem)
        return _private_key


def public_key():
    """The server's public key in the URL-safe form browsers expect."""
    return b64u(_point(_get_private_key().public_key()))


def vapid_authorization(endpoint):
    """The `Authorization` header proving this message comes from our server."""
    parsed = urllib.parse.urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps(
        {"aud": audience, "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUBJECT},
        separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    der_signature = _get_private_key().sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    signature = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={public_key()}"


# ---------------------------------------------------------------- encryption (RFC 8291)

def encrypt(payload, ua_public_b64u, auth_b64u, as_private=None, salt=None):
    """
    Encrypts `payload` (bytes) for one browser subscription.
      ua_public_b64u : the subscription's p256dh key
      auth_b64u      : the subscription's auth secret
      as_private/salt: only given by the tests (the official test vector); normally random
    Returns the complete request body (aes128gcm).
    """
    ua_public = b64u_decode(ua_public_b64u)
    auth_secret = b64u_decode(auth_b64u)

    if as_private is None:
        as_private = ec.generate_private_key(ec.SECP256R1())
    as_public = _point(as_private.public_key())
    if salt is None:
        salt = os.urandom(16)

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)

    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(ecdh_secret)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)

    record = payload + b"\x02"                      # 0x02 marks the last (only) record
    ciphertext = AESGCM(cek).encrypt(nonce, record, None)
    header = salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# ---------------------------------------------------------------- sending

class SubscriptionGone(Exception):
    """The browser says this subscription no longer exists (HTTP 404/410)."""


def endpoint_allowed(endpoint):
    """Only https push services are accepted, so nobody can make the server call random addresses.
    (Tests may set DOSELY_ALLOW_INSECURE_PUSH=1 to talk to a local fake push service.)"""
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    if endpoint.startswith("https://"):
        return True
    return os.environ.get("DOSELY_ALLOW_INSECURE_PUSH") == "1" and endpoint.startswith("http://")


def send(subscription, payload, ttl=1800):
    """Sends one notification to one subscription (a dict with endpoint, p256dh, auth)."""
    body = encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                   subscription["p256dh"], subscription["auth"])
    request = urllib.request.Request(
        subscription["endpoint"], data=body, method="POST",
        headers={
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": "high",                      # wake the phone now, even in battery saver
            "Authorization": vapid_authorization(subscription["endpoint"]),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise SubscriptionGone() from e
        raise


# Sending talks to the internet, so it happens on its own thread and never
# slows down the once-a-second reminder engine.
_queue = queue.Queue()
_worker_started = False


def notify_user(user_id, payload, ttl=1800):
    """Queues a notification for every device this user has turned notifications on for."""
    _queue.put((user_id, payload, ttl))


def _worker():
    while True:
        user_id, payload, ttl = _queue.get()
        try:
            for sub in db.get_push_subscriptions(user_id):
                try:
                    send(sub, payload, ttl)
                except SubscriptionGone:
                    db.delete_push_subscription(sub["endpoint"])
                except Exception as e:
                    print(f"[push] could not reach a device of user {user_id}: {e}")
        except Exception as e:
            print(f"[push] error: {e}")


def start():
    """Creates the server key if needed and starts the background sender."""
    global _worker_started
    _get_private_key()
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, daemon=True, name="push-sender").start()


# ---------------------------------------------------------------- the messages we send

def _digits(text):
    return "".join(ch for ch in str(text) if ch.isdigit())


def notify_reminder(reminder, snooze_minutes):
    """A dose is due (or a snoozed one is due again)."""
    medicine = reminder["medicine_name"]
    dosage = reminder["dosage"]
    body = f"{medicine}" + (f" ({dosage})" if dosage else "") + " is due now."
    notify_user(reminder["patient_id"], {
        "kind": "reminder",
        "title": "Time for your medicine",
        "body": body,
        "tag": f"dose-{reminder['prescription_id']}-{_digits(reminder['dose_time'])}",
        "reminder_id": reminder["id"],
        "snooze_minutes": snooze_minutes,
        "url": "/patient/dashboard",
    }, ttl=1800)


def notify_alert(guardian_id, reminder_id, message):
    """A patient missed a dose; tell their guardian."""
    notify_user(guardian_id, {
        "kind": "alert",
        "title": "Dosely alert",
        "body": message,
        "tag": f"alert-{reminder_id}-{guardian_id}",
        "url": "/guardian/dashboard",
    }, ttl=3600)
