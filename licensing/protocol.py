"""License ka common hissa: key format, signed token aur "kya license abhi chalta hai?" ka faisla.

Isse app (customer ke PC par) aur license server (seller ke paas) dono use karte hain, isliye yahan Django import NAHI hai.

Kaise kaam karta hai:
  * Server license ko apni PRIVATE key se sign karke ek token deta hai (Ed25519).
  * App ke andar sirf PUBLIC key hoti hai: wo token ki sign verify kar sakti hai, par naya token bana nahi sakti.
    Isliye customer apne aap license "bana" ya token me expiry badal nahi sakta.
  * Token me PC ki pehchaan (machine) hoti hai, isliye wo kisi aur PC par nahi chalta.
  * Token me 'check_by' tareekh hoti hai: us din tak server se dobara check na hua toh app internet maangti hai.
"""
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

TOKEN_VERSION = 1
TYPES = ("monthly", "yearly", "lifetime")
PENDING = "pending"        # customer ne request ki hai, seller ne abhi key nahi banayi (token tab tak nahi milta)
TYPE_LABELS = {"monthly": "Monthly plan", "yearly": "Yearly plan", "lifetime": "Lifetime plan"}

KEY_PREFIX = "EXFN"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # 0/O aur 1/I nahi: key padhne me galti na ho
CLOCK_SLACK = timedelta(days=1)                   # itna fark chalega; usse zyada ho toh clock peeche ki gayi maano


class TokenError(Exception):
    """Token kharab, badla hua, ya galat key se sign."""


# ------------------------------------------------------------------ time
def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(text):
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ license key (customer ko milne wali)
def generate_key():
    """EXFN-XXXXX-XXXXX-XXXXX-XXXXX  (20 random akshar, ~100 bit: andaza lagana namumkin)."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(20))
    return "-".join([KEY_PREFIX] + [body[i:i + 5] for i in range(0, 20, 5)])


def normalize_key(text):
    """User jo bhi type kare (chhote akshar, space, bina dash) use sahi format me lao. Galat ho toh '' dega."""
    raw = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    if not raw.startswith(KEY_PREFIX):
        raw = KEY_PREFIX + raw if len(raw) == 20 else raw
    if len(raw) != len(KEY_PREFIX) + 20 or not raw.startswith(KEY_PREFIX):
        return ""
    body = raw[len(KEY_PREFIX):]
    if any(ch not in _ALPHABET for ch in body):
        return ""
    return "-".join([KEY_PREFIX] + [body[i:i + 5] for i in range(0, 20, 5)])


def hash_key(key):
    """Server key ko seedha nahi, uska hash rakhta hai (database leak ho toh bhi keys na nikalein)."""
    return hashlib.sha256(normalize_key(key).encode()).hexdigest()


def key_hint(key):
    return "..." + normalize_key(key)[-5:]


# ------------------------------------------------------------------ signing
def _b64e(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_keypair():
    """(private, public) base64 me. Private SIRF server par; public app me daalte hain."""
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
    raw_public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64e(raw_private), _b64e(raw_public)


def public_from_private(private_b64):
    """Private key se uski public key (installer banate waqt app me daalne ke liye)."""
    raw = Ed25519PrivateKey.from_private_bytes(_b64d(private_b64)).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64e(raw)


def sign_token(payload, private_b64):
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = Ed25519PrivateKey.from_private_bytes(_b64d(private_b64)).sign(body.encode())
    return f"{body}.{_b64e(signature)}"


def verify_token(token, public_b64):
    """Sahi ho toh payload (dict) deta hai, warna TokenError."""
    try:
        body, signature = (token or "").strip().split(".")
        Ed25519PublicKey.from_public_bytes(_b64d(public_b64)).verify(_b64d(signature), body.encode())
        payload = json.loads(_b64d(body))
    except (ValueError, InvalidSignature, TypeError) as exc:   # base64 / json / signature, teeno yahin
        raise TokenError("License token is not valid") from exc
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        raise TokenError("License token version is not supported")
    return payload


def make_payload(*, license_id, license_type, customer, machine, issued, expires, check_by):
    return {
        "v": TOKEN_VERSION,
        "lid": str(license_id),
        "type": license_type,
        "customer": customer,
        "machine": machine,
        "issued": iso(issued),
        "expires": iso(expires),        # None = lifetime
        "check_by": iso(check_by),
    }


# ------------------------------------------------------------------ "license chalta hai ya nahi?"
@dataclass
class LicenseStatus:
    code: str                  # active | unlicensed | invalid | wrong_machine | expired | clock | offline_overdue | revoked
    ok: bool                   # True = app chal sakta hai
    message: str
    license_type: str = ""
    customer: str = ""
    expires: datetime = None   # None = lifetime (ya license hi nahi)
    days_left: int = None
    check_by: datetime = None
    warn: str = ""             # ok hai par jaldi dhyan dena hai (expiry paas / online check baaki)

    @property
    def type_label(self):
        return TYPE_LABELS.get(self.license_type, "")


def _fmt_date(dt):
    return dt.astimezone().strftime("%d %b %Y")      # is PC ki local tareekh me (UTC me nahi)


BLOCK_DEFAULTS = {
    "revoked": "This license has been disabled. Please contact support.",
    "expired": "Your license has expired. Please renew to continue.",
    "not_activated": "This PC is no longer activated for the license. Please enter your license key again.",
}


def evaluate(token, public_b64, machine_id, now=None, max_seen=None, blocked_code="", blocked_message="",
             warn_days=14):
    """Token dekhkar batao ki abhi app chalna chahiye ya nahi. Network ki zarurat nahi.

    blocked_code: server ne pichle check me jo roka (revoked / expired / not_activated), taaki wo turant lagu ho.
    """
    now = now or utcnow()
    if blocked_code:
        return LicenseStatus(blocked_code, False, blocked_message or BLOCK_DEFAULTS.get(blocked_code, "License is blocked."))
    if not token:
        return LicenseStatus("unlicensed", False, "No license activated yet. Enter the license key the seller sent you.")
    try:
        payload = verify_token(token, public_b64)
    except TokenError:
        return LicenseStatus("invalid", False, "The saved license is not valid. Please activate again.")

    info = dict(license_type=payload.get("type", ""), customer=payload.get("customer", ""))
    expires, check_by, issued = parse_iso(payload.get("expires")), parse_iso(payload.get("check_by")), parse_iso(payload.get("issued"))
    info.update(expires=expires, check_by=check_by)

    if payload.get("machine") != machine_id:
        return LicenseStatus("wrong_machine", False,
                             "This license is activated on a different PC. Deactivate it there, or activate again here.", **info)
    if expires and now > expires:
        return LicenseStatus("expired", False, f"Your license expired on {_fmt_date(expires)}. Please renew to continue.",
                             days_left=0, **info)
    if (issued and now < issued - CLOCK_SLACK) or (max_seen and now < max_seen - CLOCK_SLACK):
        return LicenseStatus("clock", False,
                             "The computer's date looks wrong. Fix the date and connect to the internet to verify the license.", **info)
    if check_by and now > check_by:
        return LicenseStatus("offline_overdue", False,
                             "The license could not be verified for a long time. Please connect to the internet and press 'Check now'.", **info)

    days_left = None
    warn = ""
    if expires:
        days_left = max(0, -(-int((expires - now).total_seconds()) // 86400))    # upar ki taraf round
        if days_left <= warn_days:
            warn = f"Your license expires on {_fmt_date(expires)} ({days_left} day{'s' if days_left != 1 else ''} left). Please renew."
    if not warn and check_by and (check_by - now) < timedelta(days=3):
        warn = "Please connect to the internet soon so the license can be verified."
    return LicenseStatus("active", True, "License is active.", days_left=days_left, warn=warn, **info)
