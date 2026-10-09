"""License server ka logic: activate / check / deactivate / trial. HTTP se alag rakha hai taaki test aasaan ho."""
import re
from datetime import timedelta

from django.conf import settings
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


def _ensure_usable(lic, now):
    if lic.revoked:
        raise LicenseError("revoked", lic.revoked_reason or "This license has been disabled. Please contact support.")
    if lic.expires_at and now > lic.expires_at:
        what = "free trial has ended" if lic.license_type == "trial" else "license has expired"
        raise LicenseError("expired", f"Your {what} on {lic.expires_at:%d %b %Y}. Please renew to continue.")


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


@transaction.atomic
def start_trial(machine_id, machine_name="", app_version="", ip=None, now=None):
    """Har PC ko sirf ek free trial. Dobara maange toh wahi purana trial (jo khatam ho chuka ho sakta hai)."""
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    previous = Activation.objects.filter(machine_id=machine_id, license__license_type="trial").select_related("license").first()
    if previous:
        _ensure_usable(previous.license, now)
        _touch(previous, machine_name, app_version, ip, None, now)
        return _issue(previous.license, machine_id, now)

    key = protocol.generate_key()     # trial ki key customer ko nahi dikhti, bas record ke liye
    lic = License.objects.create(
        key_hash=protocol.hash_key(key), key_hint=protocol.key_hint(key), license_type="trial",
        customer_name="Free trial", max_machines=1, duration_days=settings.TRIAL_DAYS,
        offline_grace_days=settings.OFFLINE_GRACE_DAYS, first_activated_at=now,
        expires_at=now + timedelta(days=settings.TRIAL_DAYS))
    activation = Activation(license=lic, machine_id=machine_id)
    _touch(activation, machine_name, app_version, ip, None, now)
    return _issue(lic, machine_id, now)


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
        duration_days = settings.TRIAL_DAYS if license_type == "trial" else settings.YEARLY_DAYS
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
