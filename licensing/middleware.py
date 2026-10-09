"""License khatam / block ho toh admin ke pages (aur API) band; sirf License page, login aur logout khule rehte hain."""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import service

ALLOWED_PREFIXES = (
    "/admin/login/", "/admin/logout/", "/admin/licensing/", "/admin/jsi18n/", "/admin/password_change/", "/admin/update/",
    "/static/", "/setup/",
)


class LicenseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if service.enforced() and path.startswith("/admin/") and not path.startswith(ALLOWED_PREFIXES):
            status = service.current_status()
            if not status.ok:
                if path.endswith("-api/"):      # page ke andar se aayi API call: JSON me wajah
                    return JsonResponse({"error": status.message, "license": status.code}, status=402)
                return redirect(reverse("admin:licensing_licensestate_changelist"))
            service.maybe_background_check()
            self._warn_once_a_day(request, status)
        return self.get_response(request)

    @staticmethod
    def _warn_once_a_day(request, status):
        if not status.warn or not hasattr(request, "session") or not hasattr(request, "_messages"):
            return
        from django.contrib import messages
        from django.utils import timezone
        today = timezone.localdate().isoformat()
        if request.session.get("license_warned_on") != today and request.user.is_authenticated:
            request.session["license_warned_on"] = today
            messages.warning(request, status.warn)
