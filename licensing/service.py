"""License ka dimaag (app ke andar): status nikalna, activate / check / deactivate / trial, aur usage ki ginti.

Status internet ke bina nikalta hai (signed token padhkar). Server se baat sirf activate, 24 ghante me ek check, ya
button dabane par hoti hai. Server na mile toh app 'check_by' tareekh tak (default 14 din) chalta rehta hai.
"""
import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection
from django.db.models import F
from django.utils import timezone

from . import client, protocol
from .fingerprint import machine_id, machine_name
from .models import LicenseState

logger = logging.getLogger(__name__)

STATUS_TTL = 20            # seconds: har request par token dobara verify na ho
BACKGROUND_RETRY = 300     # seconds: background check fail ho toh itne ke baad hi dobara koshish

_cache = {"status": None, "until": 0.0}
_state = {"next_bg_check": 0.0}
_bg_lock = threading.Lock()


@dataclass
class Result:
    ok: bool
    message: str


def enforced():
    return bool(getattr(settings, "LICENSE_ENFORCED", False))


def _public_key():
    return getattr(settings, "LICENSE_PUBLIC_KEY", "") or ""


def invalidate():
    _cache["until"] = 0.0


def current_status(force=False):
    """Abhi app chalna chahiye ya nahi (cached, DB ke alawa koi network nahi)."""
    if not enforced():
        return protocol.LicenseStatus("active", True, "License checks are switched off (development copy).")
    if not force and _cache["status"] is not None and time.monotonic() < _cache["until"]:
        return _cache["status"]

    state, now = LicenseState.get(), timezone.now()
    if not _public_key():
        status = protocol.LicenseStatus("invalid", False,
                                        "This copy has no license key built in. Please install the official version.")
    else:
        status = protocol.evaluate(state.token, _public_key(), machine_id(), now, state.max_seen_time,
                                   state.blocked_code, state.blocked_message, getattr(settings, "LICENSE_WARN_DAYS", 14))
    if status.ok and (state.max_seen_time is None or now - state.max_seen_time > timedelta(minutes=10)):
        LicenseState.objects.filter(pk=1).update(max_seen_time=now)   # clock peeche karne ka pata chalane ke liye
    _cache.update(status=status, until=time.monotonic() + STATUS_TTL)
    return status


# ------------------------------------------------------------------ server se baat
def _usage(state):
    return {"searches_total": state.searches_total, "scans_total": state.scans_total}


def _accept(state, data, key=None):
    """Server ka diya token verify karke save karo. Verify na ho toh (False, message)."""
    try:
        protocol.verify_token(data.get("token", ""), _public_key())
    except protocol.TokenError:
        return False, "The license server's reply could not be verified. Please check that you installed the official version."
    now = timezone.now()
    if key is not None:
        state.license_key = key
    state.token = data["token"]
    state.blocked_code = state.blocked_message = state.last_error = ""
    state.last_check_at, state.last_check_ok = now, True
    state.max_seen_time = protocol.parse_iso(data.get("server_time")) or now   # server ka time = clock guard reset
    state.save()
    invalidate()
    return True, ""


def _label(data):
    info = data.get("license") or {}
    text = protocol.TYPE_LABELS.get(info.get("type"), "License")
    expires = protocol.parse_iso(info.get("expires"))
    return f"{text}, valid until {expires.astimezone():%d %b %Y}" if expires else text


def activate(raw_key):
    key = protocol.normalize_key(raw_key)
    if not key:
        return Result(False, "That does not look like a license key. It looks like EXFN-XXXXX-XXXXX-XXXXX-XXXXX.")
    state = LicenseState.get()
    try:
        _, data = client.call("activate", {"key": key, "machine_id": machine_id(), "machine_name": machine_name(),
                                           "usage": _usage(state)})
    except client.ServerUnreachable as exc:
        return Result(False, str(exc))
    if not data.get("ok"):
        return Result(False, data.get("message") or "Activation failed.")
    ok, problem = _accept(state, data, key=key)
    return Result(True, f"License activated: {_label(data)}.") if ok else Result(False, problem)


def start_trial():
    state = LicenseState.get()
    try:
        _, data = client.call("trial", {"machine_id": machine_id(), "machine_name": machine_name()})
    except client.ServerUnreachable as exc:
        return Result(False, str(exc))
    if not data.get("ok"):
        return Result(False, data.get("message") or "Could not start the free trial.")
    state.trial_started = True
    state.license_key = ""
    ok, problem = _accept(state, data)
    return Result(True, f"Free trial started: {_label(data)}.") if ok else Result(False, problem)


def check_now():
    """Server se license dobara verify (token naya milta hai, revoke / renew yahin pata chalta hai)."""
    state = LicenseState.get()
    if not state.token and not state.license_key:
        return Result(False, "There is no license to check yet.")
    try:
        if state.license_key:
            _, data = client.call("check", {"key": state.license_key, "machine_id": machine_id(),
                                            "machine_name": machine_name(), "usage": _usage(state)})
        else:      # free trial: key nahi hoti, server wahi trial dobara deta hai
            _, data = client.call("trial", {"machine_id": machine_id(), "machine_name": machine_name()})
    except client.ServerUnreachable as exc:
        state.last_check_ok, state.last_error = False, str(exc)[:300]
        state.save(update_fields=["last_check_ok", "last_error"])
        return Result(False, str(exc))

    if data.get("ok"):
        ok, problem = _accept(state, data)
        return Result(True, "License verified.") if ok else Result(False, problem)

    error, message = data.get("error"), data.get("message") or "The license could not be verified."
    if error in ("revoked", "expired", "not_activated", "invalid_key"):
        state.blocked_code = "revoked" if error == "invalid_key" else error
        state.blocked_message = ("This license key is no longer valid. Please enter your current key." if error == "invalid_key"
                                 else message)[:300]
        state.last_check_at, state.last_check_ok, state.last_error = timezone.now(), True, ""
        state.save()
        invalidate()
    else:
        state.last_check_ok, state.last_error = False, message[:300]
        state.save(update_fields=["last_check_ok", "last_error"])
    return Result(False, message)


def deactivate():
    """Is PC se license hatao, taaki kisi aur PC par lag sake (server par seat khali hoti hai)."""
    state = LicenseState.get()
    if state.license_key:
        try:
            _, data = client.call("deactivate", {"key": state.license_key, "machine_id": machine_id()})
        except client.ServerUnreachable:
            return Result(False, "Connect to the internet to deactivate, so the license can be released for another PC.")
        if not data.get("ok") and data.get("error") not in ("invalid_key",):
            return Result(False, data.get("message") or "Could not deactivate.")
    state.license_key = state.token = state.blocked_code = state.blocked_message = state.last_error = ""
    state.last_check_at, state.last_check_ok = None, True
    state.save()
    invalidate()
    return Result(True, "This PC has been deactivated. You can now activate the license on another PC.")


def maybe_background_check():
    """Har request par chalta hai, par 24 ghante me ek hi baar asli check hota hai (alag thread me, app ruke bina)."""
    if not enforced() or time.monotonic() < _state["next_bg_check"]:
        return
    with _bg_lock:
        if time.monotonic() < _state["next_bg_check"]:
            return
        _state["next_bg_check"] = time.monotonic() + BACKGROUND_RETRY
    state = LicenseState.get()
    if not state.token and not state.license_key:
        return
    interval = timedelta(hours=getattr(settings, "LICENSE_CHECK_INTERVAL_HOURS", 24))
    if state.last_check_at and timezone.now() - state.last_check_at < interval:
        return
    threading.Thread(target=_background_worker, daemon=True).start()


def _background_worker():
    close_old_connections()
    try:
        check_now()
    except Exception:
        logger.exception("Background license check failed")
    finally:
        connection.close()


def record_usage(kind):
    """Search / scan ki ginti (sirf number, koi data nahi). Kabhi kisi feature ko rokta nahi."""
    field = {"search": "searches_total", "scan": "scans_total"}[kind]
    try:
        if not LicenseState.objects.filter(pk=1).update(**{field: F(field) + 1}):
            LicenseState.get()
            LicenseState.objects.filter(pk=1).update(**{field: F(field) + 1})
    except Exception:
        logger.exception("Could not record license usage")
