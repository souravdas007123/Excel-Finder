from django.contrib import admin, messages
from django.db.models import Max, Q, Count
from django.utils.html import format_html

from licensing import protocol

from . import core
from .models import Activation, AppRelease, License


class ActivationInline(admin.TabularInline):
    model = Activation
    extra = 0
    can_delete = False
    fields = ("machine_name", "machine_id", "app_version", "last_seen", "last_ip", "files_indexed", "searches_total",
              "scans_total", "active")
    readonly_fields = ("machine_name", "machine_id", "app_version", "last_seen", "last_ip", "files_indexed",
                       "searches_total", "scans_total")
    verbose_name_plural = "PC jis par ye license chal raha hai (untick 'active' karne par PC hat jata hai)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    """Aapke saare customers: kaun, kaun sa plan, kab tak, kitne PC, kitni files index ki. Yahin se key banate aur cancel karte ho."""
    list_display = ("customer_name", "customer_email", "plan", "state_badge", "expires_at", "pcs", "files", "searches", "last_seen")
    list_filter = ("license_type", "revoked")
    search_fields = ("customer_name", "customer_email", "key_hint", "notes")
    inlines = [ActivationInline]
    readonly_fields = ("key_hint", "first_activated_at", "created_at")
    fieldsets = [
        ("Customer", {"fields": ("customer_name", "customer_email", "notes")}),
        ("License", {"fields": ("license_type", "max_machines", "duration_days", "expires_at", "offline_grace_days")}),
        ("Cancel", {"fields": ("revoked", "revoked_reason", "key_hint", "first_activated_at", "created_at")}),
    ]
    actions = ["key_monthly", "key_yearly", "key_lifetime", "add_30_days", "add_365_days", "revoke_selected",
               "unrevoke_selected", "free_the_pc", "reset_code"]

    def get_queryset(self, request):
        active = Q(activations__active=True)
        return super().get_queryset(request).annotate(
            _active=Count("activations", filter=active), _last_seen=Max("activations__last_seen"),
            _files=Max("activations__files_indexed"), _searches=Max("activations__searches_total"),
            _scans=Max("activations__scans_total"))

    @admin.display(description="Plan", ordering="license_type")
    def plan(self, obj):
        return "Waiting for key" if obj.license_type == "pending" else obj.get_license_type_display()

    @admin.display(description="Status")
    def state_badge(self, obj):
        state = obj.state()
        label = {"pending": "Waiting for key", "unused": "Key not used yet"}.get(state, state.capitalize())
        color = {"active": "#28a745", "unused": "#6c757d", "expired": "#dc3545", "revoked": "#dc3545", "pending": "#b36b00"}[state]
        return format_html('<b style="color:{}">{}</b>', color, label)

    @admin.display(description="PCs")
    def pcs(self, obj):
        return f"{obj._active}/{obj.max_machines}"

    @admin.display(description="Files indexed", ordering="_files")
    def files(self, obj):
        return obj._files or 0

    @admin.display(description="Searches", ordering="_searches")
    def searches(self, obj):
        return obj._searches or 0

    @admin.display(description="Scans", ordering="_scans")
    def scans(self, obj):
        return obj._scans or 0

    @admin.display(description="Last seen", ordering="_last_seen")
    def last_seen(self, obj):
        return obj._last_seen

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        key = protocol.generate_key()
        obj.key_hash, obj.key_hint = protocol.hash_key(key), protocol.key_hint(key)
        if obj.license_type == "lifetime":
            obj.duration_days = None
        elif obj.license_type != "pending" and not obj.duration_days:
            obj.duration_days = core.plan_days(obj.license_type)
        super().save_model(request, obj, form, change)
        self.message_user(request, format_html(
            "License key for {} - copy it now, it will NOT be shown again: <code style='font-size:16px'>{}</code>",
            obj.customer_name, key), level=messages.SUCCESS)

    # ---- key banana (customer ne email se key maangi)
    def _generate(self, request, queryset, plan):
        for lic in queryset:
            key = core.issue_key(lic, plan)
            self.message_user(request, format_html(
                "{} - {} key (email it to {}). It will NOT be shown again: <code style='font-size:16px'>{}</code>",
                lic.customer_name, plan, lic.customer_email or "the customer", key), level=messages.SUCCESS)

    @admin.action(description="Generate key: MONTHLY (30 days from first use)")
    def key_monthly(self, request, queryset):
        self._generate(request, queryset, "monthly")

    @admin.action(description="Generate key: YEARLY (365 days from first use)")
    def key_yearly(self, request, queryset):
        self._generate(request, queryset, "yearly")

    @admin.action(description="Generate key: LIFETIME")
    def key_lifetime(self, request, queryset):
        self._generate(request, queryset, "lifetime")

    # ---- renew
    @admin.action(description="Renew: add 30 days")
    def add_30_days(self, request, queryset):
        for lic in queryset:
            core.extend(lic, 30)
        self.message_user(request, f"Added 30 days to {queryset.count()} license(s). (Lifetime licenses are unchanged.)")

    @admin.action(description="Renew: add 365 days")
    def add_365_days(self, request, queryset):
        for lic in queryset:
            core.extend(lic, 365)
        self.message_user(request, f"Added 365 days to {queryset.count()} license(s). (Lifetime licenses are unchanged.)")

    # ---- cancel / PC / password
    @admin.action(description="CANCEL (block) selected licenses")
    def revoke_selected(self, request, queryset):
        count = queryset.update(revoked=True, revoked_reason="This license has been cancelled. Please contact support.")
        self.message_user(request, f"Cancelled {count} license(s). The apps stop at their next check.", level=messages.WARNING)

    @admin.action(description="Un-cancel (unblock) selected licenses")
    def unrevoke_selected(self, request, queryset):
        self.message_user(request, f"Restored {queryset.update(revoked=False, revoked_reason='')} license(s).")

    @admin.action(description="Free the PC (customer can activate on another PC)")
    def free_the_pc(self, request, queryset):
        freed = sum(core.release_pcs(lic) for lic in queryset)
        self.message_user(request, f"Freed {freed} PC(s). The customer can now activate the key on a new PC.")

    @admin.action(description="Password reset code (customer forgot the app password)")
    def reset_code(self, request, queryset):
        for lic in queryset:
            code = core.make_reset_code(lic)
            self.message_user(request, format_html(
                "Reset code for {} (email it to {}; works once, for {} hours). It will NOT be shown again: "
                "<code style='font-size:16px'>{}</code>",
                lic.customer_name, lic.customer_email or "the customer", core.RESET_VALID_HOURS, code), level=messages.SUCCESS)


@admin.register(AppRelease)
class AppReleaseAdmin(admin.ModelAdmin):
    """Naya version yahan add karo: customers ko app ke andar 'Update available' dikhta hai."""
    list_display = ("version", "published", "one_click", "min_version", "created_at")
    list_filter = ("published",)

    @admin.display(boolean=True, description="One-click update")
    def one_click(self, obj):
        return bool(obj.sha256)
