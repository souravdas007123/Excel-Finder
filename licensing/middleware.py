"""License khatam / block ho toh admin aur naye UI (/app/) ke pages band; sirf License page, login, logout khule rehte hain."""
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import service

ALLOWED_PREFIXES = (
    "/admin/login/", "/admin/logout/", "/admin/jsi18n/", "/admin/password_change/", "/static/", "/setup/",
    "/app/license/", "/app/login/", "/app/logout/", "/app/updates/",
)
GUARDED_PREFIXES = ("/admin/", "/app/")


class LicenseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if service.enforced() and path.startswith(GUARDED_PREFIXES) and not path.startswith(ALLOWED_PREFIXES):
            status = service.current_status()
            if not status.ok:
                if status.code == "pending":      # plan ka intezaar: har 5 minute me khud dekho ki seller ne diya ya nahi
                    service.maybe_background_check()
                if request.headers.get("HX-Request") == "true":     # htmx poora page badalkar License page par jaye
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = reverse("webui:license")
                    return response
                return redirect(reverse("webui:license"))
            service.maybe_background_check()
        return self.get_response(request)
