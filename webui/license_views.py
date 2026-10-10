"""License page (account, plan, sign in / create account, key) aur 'naya version' ke buttons. Sab licensing.service se chalta hai."""
from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from licensing import service
from licensing.fingerprint import machine_id, machine_name
from licensing.models import LicenseState
from search import update_check
from search.version import VERSION

from .helpers import htmx_redirect, message_only, staff_required, toast


def _panel_context(request):
    state = LicenseState.get()
    status = service.current_status(force=True)
    return {
        "status": status, "state": state, "enforced": service.enforced(),
        "has_license": bool(state.token or state.license_key), "signed_in": bool(state.device_token),
        "account_email": state.account_email,
        "key_hint": ("EXFN-•••••-•••••-•••••-" + state.license_key[-5:]) if state.license_key else "",
        "machine_short": machine_id()[:12], "machine_name": machine_name(),
        "server_url": settings.LICENSE_SERVER_URL, "buy_url": getattr(settings, "LICENSE_BUY_URL", ""),
        "support": getattr(settings, "LICENSE_SUPPORT", ""), "can_manage": request.user.is_superuser,
        "version": VERSION, "update_enabled": update_check.enabled(),
    }


def _panel(request, extra=""):
    response = render(request, "webui/partials/_license_panel.html", _panel_context(request))
    if extra:
        response.content += extra.encode()
    return response


@staff_required
@require_GET
def license_page(request):
    return render(request, "webui/license.html", {"active": "license", **_panel_context(request)})


@staff_required
@require_GET
def license_panel(request):
    """Plan ke intezaar me panel khud har 30 second dobara aata hai. Plan mil gaya ho toh seedha app khul jata hai."""
    ctx = _panel_context(request)
    if request.GET.get("from") == "pending" and ctx["status"].ok:
        return htmx_redirect("/app/")
    return render(request, "webui/partials/_license_panel.html", ctx)


def _admin_only(view):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return message_only("Only an administrator can change the license.", "bad")
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def _result(request, result):
    """Action ka natija: panel naya + notification."""
    response = _panel(request, toast(result.message, "ok" if result.ok else "bad"))
    if result.ok and service.current_status(force=True).ok and service.enforced():
        response["HX-Trigger"] = "licenseOk"
    return response


@staff_required
@require_POST
@_admin_only
def license_check(request):
    return _result(request, service.check_now())


@staff_required
@require_POST
@_admin_only
def license_login(request):
    return _result(request, service.login(request.POST.get("email", ""), request.POST.get("password", "")))


@staff_required
@require_POST
@_admin_only
def license_signout(request):
    return _result(request, service.deactivate())


# ------------------------------------------------------------------ naya version
def _banner(request, message="", kind="info"):
    response = render(request, "webui/partials/_update_banner.html", {"oob": True})
    if message:
        response.content += toast(message, kind).encode()
    return response


@staff_required
@require_POST
def update_check_now(request):
    if not update_check.enabled():
        return message_only("Update checking is not set up in this copy.", "info")
    result = update_check.check_now()
    return _banner(request, result.message, "ok" if result.ok else "bad")


@staff_required
@require_POST
def update_dismiss(request):
    update_check.dismiss(request.POST.get("version", ""))
    return _banner(request)
