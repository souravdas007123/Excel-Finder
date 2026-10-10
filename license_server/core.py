"""License server ka logic: request (naam + email), key banana, activate / check / deactivate, password reset code.
HTTP se alag rakha hai taaki test aasaan ho."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from licensing import protocol

from .models import Activation, License

MACHINE_RE = re.compile(r"^[0-9a-f]{64}$")


class LicenseError(Exception):
    def __init__(self, code, message, http=403):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _clean_machine(machine_id):
    machine_id = (machine_id or "").strip().lower()
    if not MACHINE_RE.match(machine_id):
        raise LicenseError("bad_request", "Invalid machine id.", 400)
    return machine_id


def find_license(key):
    normalized = protocol.normalize_key(key)
    lic = License.objects.filter(key_hash=protocol.hash_key(normalized)).first() if normalized else None
    if not lic:
        raise LicenseError("invalid_key", "This license key is not valid. Please check it and try again.", 404)
    return lic


PENDING_MESSAGE = "Your license is not ready yet. Please wait for the license key from the seller."


def _ensure_usable(lic, now):
    if lic.revoked:
        raise LicenseError("revoked", lic.revoked_reason or "This license has been disabled. Please contact support.")
    if lic.license_type == protocol.PENDING:
        raise LicenseError("pending", PENDING_MESSAGE)
    if lic.expires_at and now > lic.expires_at:
        raise LicenseError("expired", f"Your license expired on {timezone.localtime(lic.expires_at):%d %b %Y}. Please renew to continue.")


def _issue(lic, machine_id, now):
    if not settings.LICENSE_PRIVATE_KEY:
        raise LicenseError("server_error", "License server is not configured (missing signing key).", 500)
    check_by = now + timedelta(days=lic.offline_grace_days)
    if lic.expires_at:
        check_by = min(check_by, lic.expires_at)    # expiry ke baad check ka matlab nahi
    payload = protocol.make_payload(
        license_id=lic.id, license_type=lic.license_type, customer=lic.customer_name, machine=machine_id,
        issued=now, expires=lic.expires_at, check_by=check_by)
    return {
        "ok": True,
        "token": protocol.sign_token(payload, settings.LICENSE_PRIVATE_KEY),
        "server_time": protocol.iso(now),
        "license": {"type": lic.license_type, "customer": lic.customer_name, "expires": protocol.iso(lic.expires_at)},
    }


def _touch(activation, machine_name, app_version, ip, usage, now):
    activation.last_seen = now
    activation.last_ip = ip or None
    if machine_name:
        activation.machine_name = machine_name[:200]
    if app_version:
        activation.app_version = app_version[:40]
    usage = usage if isinstance(usage, dict) else {}
    for field in ("searches_total", "scans_total"):     # sirf ginti (data nahi). Reinstall par ghate nahi, isliye max
        try:
            value = max(0, int(usage.get(field, 0)))
        except (TypeError, ValueError):
            value = 0
        setattr(activation, field, max(getattr(activation, field), min(value, 2_000_000_000)))
    try:                                                # files_indexed = abhi ki ginti (ghat bhi sakti hai, jaise index saaf karne par)
        activation.files_indexed = min(max(0, int(usage.get("files_indexed", activation.files_indexed))), 2_000_000_000)
    except (TypeError, ValueError):
        pass
    activation.save()


@transaction.atomic
def activate(key, machine_id, machine_name="", app_version="", ip=None, usage=None, now=None):
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    lic = License.objects.select_for_update().get(pk=find_license(key).pk)
    _ensure_usable(lic, now)

    activation = lic.activations.filter(machine_id=machine_id).first()
    if activation is None or not activation.active:
        used = lic.activations.filter(active=True).count()
        if used >= lic.max_machines:
            raise LicenseError(
                "machine_limit",
                f"This license is already used on {used} PC{'s' if used != 1 else ''} (limit {lic.max_machines}). "
                "Deactivate it on the old PC first, or contact support.", 409)
        if activation is None:
            activation = Activation(license=lic, machine_id=machine_id)
        activation.active = True
    if lic.first_activated_at is None:     # pehli activation se din ginte hain
        lic.first_activated_at = now
        if lic.expires_at is None and lic.duration_days:
            lic.expires_at = now + timedelta(days=lic.duration_days)
        lic.save()
    _touch(activation, machine_name, app_version, ip, usage, now)
    return _issue(lic, machine_id, now)


def check(key, machine_id, machine_name="", app_version="", ip=None, usage=None, now=None):
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    lic = find_license(key)
    _ensure_usable(lic, now)
    activation = lic.activations.filter(machine_id=machine_id, active=True).first()
    if activation is None:
        raise LicenseError("not_activated", "This PC is not activated for this license. Please activate again.", 409)
    _touch(activation, machine_name, app_version, ip, usage, now)
    return _issue(lic, machine_id, now)


def deactivate(key, machine_id, now=None):
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    lic = find_license(key)
    updated = lic.activations.filter(machine_id=machine_id, active=True).update(active=False, last_seen=now)
    return {"ok": True, "deactivated": bool(updated)}


def plan_days(plan):
    return settings.MONTHLY_DAYS if plan == "monthly" else settings.YEARLY_DAYS


def extend(lic, days, now=None):
    """Renewal: bacha hua time khoye bina days jodo."""
    now = now or timezone.now()
    if lic.license_type == "lifetime":
        return lic
    if lic.expires_at:
        lic.expires_at = max(lic.expires_at, now) + timedelta(days=days)
    else:                                  # abhi activate nahi hua: activation par ye din milenge
        lic.duration_days = (lic.duration_days or 0) + days
    lic.save()
    return lic


def create_license(*, customer_name, license_type="yearly", customer_email="", max_machines=1, duration_days=None,
                   notes="", offline_grace_days=None):
    """Nayi license banao. (License, key) deta hai: key SIRF abhi dikhti hai, baad me sirf hash bachta hai."""
    if license_type not in protocol.TYPES:
        raise ValueError(f"license_type must be one of {protocol.TYPES}")
    if license_type == "lifetime":
        duration_days = None
    elif not duration_days:
        duration_days = plan_days(license_type)
    key = protocol.generate_key()
    lic = License.objects.create(
        key_hash=protocol.hash_key(key), key_hint=protocol.key_hint(key), license_type=license_type,
        customer_name=customer_name, customer_email=customer_email, max_machines=max_machines,
        duration_days=duration_days, notes=notes,
        offline_grace_days=offline_grace_days or settings.OFFLINE_GRACE_DAYS)
    return lic, key


def rotate_key(lic):
    """Nayi key (purani band). Customer ki key leak / kho jaye toh."""
    key = protocol.generate_key()
    lic.key_hash, lic.key_hint = protocol.hash_key(key), protocol.key_hint(key)
    lic.save(update_fields=["key_hash", "key_hint"])
    return key


def get_license(ref):
    """Admin / command ke liye: license ko id (shuruaati hisse se) ya key ke hint se dhundho."""
    ref = (ref or "").strip()
    matches = [lic for lic in License.objects.all()
               if str(lic.id).startswith(ref.lower()) or lic.key_hint.upper() == ref.upper() or lic.key_hint.upper().endswith(ref.upper())]
    if not ref or len(matches) != 1:
        raise LookupError(f"'{ref}' se {len(matches)} license mile. Poori id ya key ke aakhri 5 akshar do.")
    return matches[0]


# ------------------------------------------------------------------ customer ki request, key banana, password reset
RESET_VALID_HOURS = 24
RESET_FAIL_LIMIT = 6          # itni galat koshish ke baad 15 minute ruko
RESET_FAIL_WINDOW = 15 * 60
_RESET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _clean_email(email):
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise LicenseError("bad_email", "Please enter a valid email address.", 400)
    return email[:254]


def _by_email(email):
    return License.objects.filter(customer_email__iexact=email).order_by("-created_at").first()


@transaction.atomic
def request_license(name, email):
    """Customer ne app me naam + email diya: seller ke admin panel me 'Waiting for key' ki row. Dobara bhejne par wahi row."""
    email = _clean_email(email)
    name = (name or "").strip()[:200]
    if not name:
        raise LicenseError("bad_name", "Please enter your name.", 400)
    if _by_email(email) is None:
        hidden_key = protocol.generate_key()      # asli key seller baad me banata hai; ye kisi ko dikhti nahi
        License.objects.create(
            key_hash=protocol.hash_key(hidden_key), key_hint=protocol.key_hint(hidden_key), license_type=protocol.PENDING,
            customer_name=name, customer_email=email, max_machines=1, offline_grace_days=settings.OFFLINE_GRACE_DAYS)
    return {"ok": True, "message": "Request received. The seller will email you a license key."}


def issue_key(lic, plan):
    """Seller 'Generate key' dabata hai: plan lagao aur nayi key banao. Din pehli activation se ginte hain. Key sirf abhi dikhti hai."""
    if plan not in protocol.TYPES:
        raise ValueError(f"plan must be one of {protocol.TYPES}")
    lic.license_type = plan
    lic.duration_days = None if plan == "lifetime" else plan_days(plan)
    lic.expires_at = None
    lic.first_activated_at = None
    lic.save()
    return rotate_key(lic)


def release_pcs(lic):
    """Is license ke saare PC hata do (customer naye PC par activate kar sake)."""
    return lic.activations.filter(active=True).update(active=False)


def make_reset_code(lic, now=None):
    """Customer password bhool gaya: seller ek baar chalne wala code banata hai (24 ghante). Code sirf abhi dikhta hai."""
    now = now or timezone.now()
    raw = "".join(secrets.choice(_RESET_ALPHABET) for _ in range(8))
    lic.reset_code_hash = hashlib.sha256(raw.encode()).hexdigest()
    lic.reset_expires = now + timedelta(hours=RESET_VALID_HOURS)
    lic.save(update_fields=["reset_code_hash", "reset_expires"])
    return f"{raw[:4]}-{raw[4:]}"


def _reset_throttle_key(email):
    return f"reset-fail:{hashlib.sha256(email.encode()).hexdigest()[:20]}"


def verify_reset(email, code, now=None):
    """App ne email + reset code bheja: sahi ho toh code kharch ho jata hai (dobara nahi chalega) aur app naya password rakh sakta hai."""
    now = now or timezone.now()
    email = _clean_email(email)
    fails = cache.get(_reset_throttle_key(email), 0)
    if fails >= RESET_FAIL_LIMIT:
        raise LicenseError("too_many_attempts", "Too many wrong codes. Please wait 15 minutes and try again.", 429)
    raw = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    lic = _by_email(email)
    good = bool(lic and raw and lic.reset_code_hash and lic.reset_expires and now <= lic.reset_expires
                and hmac.compare_digest(lic.reset_code_hash, hashlib.sha256(raw.encode()).hexdigest()))
    if not good:
        cache.set(_reset_throttle_key(email), fails + 1, RESET_FAIL_WINDOW)
        raise LicenseError("bad_code", "That reset code is not valid or has expired. Ask the seller for a new one.", 403)
    cache.delete(_reset_throttle_key(email))
    lic.reset_code_hash, lic.reset_expires = "", None
    lic.save(update_fields=["reset_code_hash", "reset_expires"])
    return {"ok": True, "message": "Reset code accepted."}


# ------------------------------------------------------------------ app releases (update)
def latest_release():
    """Sabse naya published AppRelease, ya None."""
    from .models import AppRelease

    def order(release):
        return tuple(int(p) if p.isdigit() else 0 for p in release.version.split("."))

    releases = list(AppRelease.objects.filter(published=True))
    return max(releases, key=order) if releases else None


def release_manifest(release):
    if release is None:
        return {"none": True}
    return {
        "version": release.version, "released": release.created_at.date().isoformat(),
        "download_url": release.download_url, "sha256": release.sha256.strip().lower(),
        "notes": [line.strip() for line in release.notes.splitlines() if line.strip()],
        "min_version": release.min_version.strip(),
    }
