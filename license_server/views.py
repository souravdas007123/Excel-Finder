import json

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import core


def _client_ip(request):
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR")


def _rate_limited(ip):
    """Ek IP se ek minute me limit se zyada requests: key guess karne ya spam karne wale ko rokta hai."""
    bucket = f"license-rate:{ip}:{int(timezone.now().timestamp() // 60)}"
    cache.add(bucket, 0, timeout=90)
    try:
        return cache.incr(bucket) > settings.RATE_LIMIT_PER_MINUTE
    except ValueError:
        return False


def _fail(code, message, status):
    return JsonResponse({"ok": False, "error": code, "message": message}, status=status)


@require_GET
def ping(request):
    return JsonResponse({"ok": True, "time": timezone.now().isoformat()})


@csrf_exempt
@require_POST
def api(request, action):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return _fail("rate_limited", "Too many requests. Please wait a minute and try again.", 429)
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _fail("bad_request", "Request must be a JSON object.", 400)

    common = dict(machine_id=data.get("machine_id"), ip=ip)
    meta = dict(machine_name=str(data.get("machine_name", ""))[:200], app_version=str(data.get("app_version", ""))[:40])
    try:
        if action == "activate":
            result = core.activate(data.get("key"), usage=data.get("usage"), **common, **meta)
        elif action == "check":
            result = core.check(data.get("key"), usage=data.get("usage"), **common, **meta)
        elif action == "deactivate":
            result = core.deactivate(data.get("key"), data.get("machine_id"))
        elif action == "trial":
            result = core.start_trial(**common, **meta)
        elif action == "register":
            result = core.register(data.get("name"), data.get("email"), data.get("password"), usage=data.get("usage"), **common, **meta)
        elif action == "login":
            result = core.login(data.get("email"), data.get("password"), usage=data.get("usage"), **common, **meta)
        elif action == "account_check":
            result = core.account_check(data.get("email"), data.get("device_token"), usage=data.get("usage"), **common, **meta)
        elif action == "logout":
            result = core.account_logout(data.get("email"), data.get("device_token"), data.get("machine_id"))
        else:
            return _fail("not_found", "Unknown action.", 404)
    except core.LicenseError as exc:
        return _fail(exc.code, exc.message, exc.http)
    return JsonResponse(result)


@require_GET
def latest(request):
    """Naya app version (app ye padhkar 'Update' ka option dikhata hai). Admin panel ke 'App releases' se aata hai."""
    if _rate_limited(_client_ip(request)):
        return _fail("rate_limited", "Too many requests. Please wait a minute and try again.", 429)
    return JsonResponse(core.release_manifest(core.latest_release()))
