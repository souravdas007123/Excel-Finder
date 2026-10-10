from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from search.version import VERSION

from . import service
from .fingerprint import machine_id, machine_name
from .models import LicenseState


@admin.register(LicenseState)
class LicenseAdmin(admin.ModelAdmin):
    """Sidebar wala 'License' page: status, key daalna, check, deactivate."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff    # license ki halat har staff dekh sake

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("activate/", wrap(self._action(lambda r: service.activate(r.POST.get("key", "")))), name="licensing_activate"),
            path("check/", wrap(self._action(lambda r: service.check_now())), name="licensing_check"),
            path("deactivate/", wrap(self._action(lambda r: service.deactivate())), name="licensing_deactivate"),
        ] + super().get_urls()

    @staticmethod
    def _action(run):
        @require_POST
        def view(request):
            if not request.user.is_superuser:       # license badalna sirf admin (superuser) ka kaam
                messages.error(request, "Only an administrator can change the license.")
            else:
                result = run(request)
                (messages.success if result.ok else messages.error)(request, result.message)
            return HttpResponseRedirect(reverse("admin:licensing_licensestate_changelist"))
        return view

    def changelist_view(self, request, extra_context=None):
        state = LicenseState.get()
        status = service.current_status(force=True)
        context = {
            **self.admin_site.each_context(request),
            "title": "License",
            "opts": self.model._meta,
            "status": status,
            "state": state,
            "enforced": service.enforced(),
            "has_license": bool(state.token or state.license_key),
            "account_email": state.account_email,
            "account_name": state.account_name,
            "key_hint": ("EXFN-•••••-•••••-•••••-" + state.license_key[-5:]) if state.license_key else "",
            "machine_short": machine_id()[:12],
            "machine_name": machine_name(),
            "server_url": settings.LICENSE_SERVER_URL,
            "buy_url": getattr(settings, "LICENSE_BUY_URL", ""),
            "support": getattr(settings, "LICENSE_SUPPORT", ""),
            "can_manage": request.user.is_superuser,
            "version": VERSION,
        }
        return TemplateResponse(request, "admin/licensing/license.html", context)
