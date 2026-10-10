"""License server ka logic: activate / check / deactivate / trial. HTTP se alag rakha hai taaki test aasaan ho."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.auth import password_validation
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


PENDING_MESSAGE = ("Your account is created and waiting for approval. "
                   "You will get access as soon as the seller activates your plan.")


def _ensure_usable(lic, now):
    if lic.revoked:
        raise LicenseError("revoked", lic.revoked_reason or "This license has been disabled. Please contact support.")
    if lic.license_type == protocol.PENDING:
        raise LicenseError("pending", PENDING_MESSAGE)
    if lic.expires_at and now > lic.expires_at:
        what = "free trial has ended" if lic.license_type == "trial" else "license has expired"
        raise LicenseError("expired", f"Your {what} on {timezone.localtime(lic.expires_at):%d %b %Y}. Please renew to continue.")


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


def plan_days(plan):
    return {"trial": settings.TRIAL_DAYS, "monthly": settings.MONTHLY_DAYS}.get(plan, settings.YEARLY_DAYS)


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


# ------------------------------------------------------------------ accounts (email + password), plan admin panel se
LOGIN_FAIL_LIMIT = 6          # itni galat koshish ke baad 15 minute ruko
LOGIN_FAIL_WINDOW = 15 * 60
_DUMMY_HASH = make_password("not-a-real-password")


def _clean_email(email):
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise LicenseError("bad_email", "Please enter a valid email address.", 400)
    return email[:254]


def _account(email):
    return License.objects.filter(password_hash__gt="", customer_email__iexact=email).first()


def _secret_hash(secret):
    return hashlib.sha256((secret or "").encode()).hexdigest()


def _account_info(lic):
    return {"email": lic.customer_email, "name": lic.customer_name, "plan": lic.license_type,
            "expires": protocol.iso(lic.expires_at)}


def _login_device(lic, machine_id, machine_name, app_version, ip, usage, now):
    """Is PC ko account se jodo (machine limit ke andar) aur uska private secret do. Pending account ko bhi."""
    activation = lic.activations.filter(machine_id=machine_id).first()
    if activation is None or not activation.active:
        used = lic.activations.filter(active=True).count()
        if used >= lic.max_machines:
            raise LicenseError(
                "machine_limit",
                f"This account is already signed in on {used} PC{'s' if used != 1 else ''} (limit {lic.max_machines}). "
                "Sign out on the old PC first, or contact support.", 409)
        if activation is None:
            activation = Activation(license=lic, machine_id=machine_id)
        activation.active = True
    secret = secrets.token_urlsafe(32)
    activation.secret_hash = _secret_hash(secret)
    _touch(activation, machine_name, app_version, ip, usage, now)
    return secret


def _account_reply(lic, machine_id, secret, now):
    if lic.revoked:
        raise LicenseError("revoked", lic.revoked_reason or "This account has been disabled. Please contact support.")
    if lic.license_type == protocol.PENDING:     # account hai, par plan abhi nahi mila: login ho gaya, access nahi
        return {"ok": True, "pending": True, "message": PENDING_MESSAGE, "device_token": secret,
                "account": _account_info(lic)}
    _ensure_usable(lic, now)
    reply = _issue(lic, machine_id, now)
    reply.update(device_token=secret, account=_account_info(lic))
    return reply


@transaction.atomic
def register(name, email, password, machine_id, machine_name="", app_version="", ip=None, usage=None, now=None):
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    email = _clean_email(email)
    name = (name or "").strip()[:200] or email
    try:
        password_validation.validate_password(password or "")
    except ValidationError as exc:
        raise LicenseError("weak_password", " ".join(exc.messages), 400)
    if _account(email):
        raise LicenseError("email_exists", "An account with this email already exists. Please sign in instead.", 409)
    hidden_key = protocol.generate_key()      # account ki key koi nahi dekhta: bas table ke liye
    lic = License.objects.create(
        key_hash=protocol.hash_key(hidden_key), key_hint=protocol.key_hint(hidden_key), license_type=protocol.PENDING,
        customer_name=name, customer_email=email, password_hash=make_password(password), max_machines=1,
        offline_grace_days=settings.OFFLINE_GRACE_DAYS)
    secret = _login_device(lic, machine_id, machine_name, app_version, ip, usage, now)
    return _account_reply(lic, machine_id, secret, now)


def _throttle_key(email):
    return f"acct-fail:{hashlib.sha256(email.encode()).hexdigest()[:20]}"


@transaction.atomic
def login(email, password, machine_id, machine_name="", app_version="", ip=None, usage=None, now=None):
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    email = _clean_email(email)
    fails = cache.get(_throttle_key(email), 0)
    if fails >= LOGIN_FAIL_LIMIT:
        raise LicenseError("too_many_attempts", "Too many wrong passwords. Please wait 15 minutes and try again.", 429)
    lic = _account(email)
    good = check_password(password or "", lic.password_hash if lic else _DUMMY_HASH)   # time barabar rakhne ke liye
    if not lic or not good:
        cache.set(_throttle_key(email), fails + 1, LOGIN_FAIL_WINDOW)
        raise LicenseError("bad_login", "Wrong email or password.", 401)
    cache.delete(_throttle_key(email))
    lic = License.objects.select_for_update().get(pk=lic.pk)
    if lic.revoked:
        raise LicenseError("revoked", lic.revoked_reason or "This account has been disabled. Please contact support.")
    secret = _login_device(lic, machine_id, machine_name, app_version, ip, usage, now)
    return _account_reply(lic, machine_id, secret, now)


def account_check(email, device_token, machine_id, machine_name="", app_version="", ip=None, usage=None, now=None):
    """Daily check: pending ho toh pending, plan mila ho toh naya signed token."""
    now = now or timezone.now()
    machine_id = _clean_machine(machine_id)
    lic = _account(_clean_email(email))
    activation = lic.activations.filter(machine_id=machine_id, active=True).first() if lic else None
    if not activation or not activation.secret_hash or not hmac.compare_digest(activation.secret_hash, _secret_hash(device_token)):
        raise LicenseError("not_activated", "This PC is not signed in. Please sign in again.", 409)
    _touch(activation, machine_name, app_version, ip, usage, now)
    return _account_reply(lic, machine_id, device_token, now)


def account_logout(email, device_token, machine_id):
    machine_id = _clean_machine(machine_id)
    lic = _account(_clean_email(email))
    activation = lic.activations.filter(machine_id=machine_id, active=True).first() if lic else None
    if activation and activation.secret_hash and hmac.compare_digest(activation.secret_hash, _secret_hash(device_token)):
        activation.active = False
        activation.save(update_fields=["active"])
        return {"ok": True, "deactivated": True}
    return {"ok": True, "deactivated": False}


def set_plan(lic, plan, now=None, days=None):
    """Seller ka plan dena: monthly / yearly / lifetime (renew bhi: bacha hua time khoye bina din jodta hai)."""
    now = now or timezone.now()
    if plan not in ("pending", "trial") + tuple(p for p in protocol.TYPES):
        raise ValueError(f"unknown plan {plan}")
    if plan == "lifetime":
        lic.expires_at, lic.duration_days = None, None
    elif plan == protocol.PENDING:
        lic.expires_at = None
    else:
        add = timedelta(days=days or plan_days(plan))
        same_plan_running = lic.license_type == plan and lic.expires_at and lic.expires_at > now
        lic.expires_at = (lic.expires_at if same_plan_running else now) + add
        lic.duration_days = days or plan_days(plan)
    lic.license_type = plan
    lic.save()                      # block (revoked) ko ye nahi chhedta: block alag se hatana padta hai
    return lic


def set_password(lic, new_password=None):
    """Customer password bhool jaye: seller naya temporary password deta hai. (password, ) wapas."""
    new_password = new_password or secrets.token_urlsafe(9)
    lic.password_hash = make_password(new_password)
    lic.save(update_fields=["password_hash"])
    cache.delete(_throttle_key((lic.customer_email or "").lower()))
    return new_password


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
