"""License khatam / na ho toh app ke pages (/app/) band; sirf License page, login, logout, password reset aur update khule rehte hain."""
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import service

ALLOWED_PREFIXES = (
    "/static/", "/setup/",
    "/app/license/", "/app/login/", "/app/logout/", "/app/forgot/", "/app/updates/",
)
GUARDED_PREFIXES = ("/app/",)


class LicenseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if service.enforced() and path.startswith(GUARDED_PREFIXES) and not path.startswith(ALLOWED_PREFIXES):
            status = service.current_status()
            if not status.ok:
                if request.headers.get("HX-Request") == "true":     # htmx poora page badalkar License page par jaye
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = reverse("webui:license")
                    return response
                return redirect(reverse("webui:license"))
            service.maybe_background_check()
        return self.get_response(request)
