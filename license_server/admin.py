from django.contrib import admin, messages
from django.db.models import Count, Max, Q
from django.utils.html import format_html

from licensing import protocol

from . import core
from .models import Account, Activation, AppRelease, License


class ActivationInline(admin.TabularInline):
    model = Activation
    extra = 0
    can_delete = False
    fields = ("machine_name", "machine_id", "app_version", "last_seen", "last_ip", "searches_total", "scans_total", "active")
    readonly_fields = ("machine_name", "machine_id", "app_version", "last_seen", "last_ip", "searches_total", "scans_total")
    verbose_name_plural = "PCs using this license (untick 'active' to free the seat)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = ("customer_name", "license_type", "key_hint", "state_badge", "expires_at", "pcs", "last_seen", "created_at")
    list_filter = ("license_type", "revoked")
    search_fields = ("customer_name", "customer_email", "key_hint", "notes")
    inlines = [ActivationInline]
    readonly_fields = ("key_hint", "first_activated_at", "created_at")
    fieldsets = [
        ("Customer", {"fields": ("customer_name", "customer_email", "notes")}),
        ("License", {"fields": ("license_type", "max_machines", "duration_days", "expires_at", "offline_grace_days")}),
        ("Status", {"fields": ("revoked", "revoked_reason", "key_hint", "first_activated_at", "created_at")}),
    ]
    actions = ["extend_one_year", "revoke_selected", "unrevoke_selected", "issue_new_key"]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(password_hash="").annotate(    # accounts alag list me
            _active=Count("activations", filter=Q(activations__active=True)), _last_seen=Max("activations__last_seen"))

    @admin.display(description="Status")
    def state_badge(self, obj):
        state = obj.state()
        color = {"active": "#28a745", "unused": "#6c757d", "expired": "#dc3545", "revoked": "#dc3545", "pending": "#b36b00"}[state]
        return format_html('<b style="color:{}">{}</b>', color, state.capitalize())

    @admin.display(description="PCs")
    def pcs(self, obj):
        return f"{obj._active}/{obj.max_machines}"

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
        elif not obj.duration_days:
            obj.duration_days = 14 if obj.license_type == "trial" else 365
        super().save_model(request, obj, form, change)
        self.message_user(request, format_html(
            "License key for {} - copy it now, it will NOT be shown again: <code style='font-size:16px'>{}</code>",
            obj.customer_name, key), level=messages.SUCCESS)

    @admin.action(description="Renew: extend by 1 year (+365 days)")
    def extend_one_year(self, request, queryset):
        for lic in queryset:
            core.extend(lic, 365)
        self.message_user(request, f"Extended {queryset.count()} license(s) by 365 days. (Lifetime licenses are unchanged.)")

    @admin.action(description="Revoke (block) selected licenses")
    def revoke_selected(self, request, queryset):
        count = queryset.update(revoked=True, revoked_reason="This license has been disabled. Please contact support.")
        self.message_user(request, f"Revoked {count} license(s). Apps will be blocked at their next check.", level=messages.WARNING)

    @admin.action(description="Un-revoke selected licenses")
    def unrevoke_selected(self, request, queryset):
        self.message_user(request, f"Restored {queryset.update(revoked=False, revoked_reason='')} license(s).")

    @admin.action(description="Issue a NEW key (old key stops working)")
    def issue_new_key(self, request, queryset):
        for lic in queryset:
            key = core.rotate_key(lic)
            self.message_user(request, format_html(
                "New key for {} - copy it now, it will NOT be shown again: <code style='font-size:16px'>{}</code>",
                lic.customer_name, key), level=messages.SUCCESS)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """Customers jinhone app me account banaya. Yahan se plan do: Monthly / Yearly / Lifetime (ya block)."""
    list_display = ("customer_email", "customer_name", "license_type", "state_badge", "expires_at", "pcs", "last_seen", "created_at")
    list_display_links = ("customer_email",)
    list_editable = ("license_type",)
    list_filter = ("license_type", "revoked")
    search_fields = ("customer_name", "customer_email", "notes")
    inlines = [ActivationInline]
    readonly_fields = ("created_at",)
    fieldsets = [
        ("Customer", {"fields": ("customer_name", "customer_email", "notes")}),
        ("Plan", {"fields": ("license_type", "expires_at", "max_machines"),
                  "description": "Plan badalte hi din apne aap lag jate hain (Monthly 30, Yearly 365, Lifetime kabhi nahi). "
                                 "Khud tareekh badalni ho toh 'Expires at' badlo."}),
        ("Block", {"fields": ("revoked", "revoked_reason", "created_at")}),
    ]
    actions = ["give_monthly", "give_yearly", "give_lifetime", "revoke_selected", "unrevoke_selected", "reset_password"]

    def has_add_permission(self, request):
        return False          # customer khud app me account banata hai

    def get_queryset(self, request):
        return super().get_queryset(request).filter(password_hash__gt="").annotate(
            _active=Count("activations", filter=Q(activations__active=True)), _last_seen=Max("activations__last_seen"))

    @admin.display(description="Status")
    def state_badge(self, obj):
        state = obj.state()
        label = {"pending": "Waiting for approval"}.get(state, state.capitalize())
        color = {"active": "#28a745", "unused": "#28a745", "expired": "#dc3545", "revoked": "#dc3545", "pending": "#b36b00"}[state]
        return format_html('<b style="color:{}">{}</b>', color, label)

    @admin.display(description="PCs")
    def pcs(self, obj):
        return f"{obj._active}/{obj.max_machines}"

    @admin.display(description="Last seen", ordering="_last_seen")
    def last_seen(self, obj):
        return obj._last_seen

    def save_model(self, request, obj, form, change):
        if change and "license_type" in form.changed_data and "expires_at" not in form.changed_data:
            new_plan = obj.license_type
            obj.license_type = form.initial.get("license_type", new_plan)     # purana plan, taaki renew sahi gine
            core.set_plan(obj, new_plan)
        else:
            super().save_model(request, obj, form, change)

    def _give(self, request, queryset, plan):
        for account in queryset:
            core.set_plan(account, plan)
        self.message_user(request, f"{queryset.count()} account(s) now on the {plan} plan.")

    @admin.action(description="Give MONTHLY access (+30 days)")
    def give_monthly(self, request, queryset):
        self._give(request, queryset, "monthly")

    @admin.action(description="Give YEARLY access (+365 days)")
    def give_yearly(self, request, queryset):
        self._give(request, queryset, "yearly")

    @admin.action(description="Give LIFETIME access")
    def give_lifetime(self, request, queryset):
        self._give(request, queryset, "lifetime")

    @admin.action(description="Block selected accounts")
    def revoke_selected(self, request, queryset):
        count = queryset.update(revoked=True, revoked_reason="This account has been disabled. Please contact support.")
        self.message_user(request, f"Blocked {count} account(s). Their apps stop at the next check.", level=messages.WARNING)

    @admin.action(description="Unblock selected accounts")
    def unrevoke_selected(self, request, queryset):
        self.message_user(request, f"Unblocked {queryset.update(revoked=False, revoked_reason='')} account(s).")

    @admin.action(description="Set a new temporary password (forgot password)")
    def reset_password(self, request, queryset):
        for account in queryset:
            password = core.set_password(account)
            self.message_user(request, format_html(
                "New password for {} - send it to the customer, it will NOT be shown again: <code style='font-size:16px'>{}</code>",
                account.customer_email, password), level=messages.SUCCESS)


@admin.register(AppRelease)
class AppReleaseAdmin(admin.ModelAdmin):
    """Naya version yahan add karo: customers ko app ke andar 'Update available' dikhta hai."""
    list_display = ("version", "published", "one_click", "min_version", "created_at")
    list_filter = ("published",)

    @admin.display(boolean=True, description="One-click update")
    def one_click(self, obj):
        return bool(obj.sha256)
