import re

from django.contrib import admin

from .drives import get_scan_locations
from .models import BulkSearch, FileIndex, NumberIndex, ScanTask
from .scanner import MIN_DIGITS, scan_lock
from .sidebar import build_sidebar
from .views import MAX_NUMBERS, custom_path_allowed


@admin.register(FileIndex)
class FileIndexAdmin(admin.ModelAdmin):
    list_display = ("file_name", "file_path", "file_size", "last_scanned")
    search_fields = ("file_name", "file_path")
    search_help_text = "File name, path, ya number (exact match) se search karo. Kai numbers comma/space se alag karo."
    list_per_page = 50
    show_full_result_count = False   # badi table par extra COUNT query nahi chalegi
    change_list_template = "admin/file_index_changelist.html"

    def get_search_results(self, request, queryset, search_term):
        found, may_have_duplicates = super().get_search_results(request, queryset, search_term)

        terms = {re.sub(r"\D", "", t) for t in re.split(r"[,;\s]+", search_term)}
        terms.discard("")
        if terms:
            file_ids = NumberIndex.objects.filter(number__in=terms).values("file_id")
            found = found | queryset.filter(pk__in=file_ids)
        return found, may_have_duplicates

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["scan_locations"] = [
            (key, loc["label"]) for key, loc in get_scan_locations().items()
        ]
        extra_context["allow_custom_path"] = custom_path_allowed(request.user)
        extra_context["can_clear_index"] = self.has_delete_permission(request)
        # Page refresh ke baad bhi chalta hua scan dikhane ke liye (lock sach me locked ho tabhi scan "chal raha" maano)
        extra_context["running_task_id"] = (
            ScanTask.objects.filter(status="Running").order_by("-id").values_list("id", flat=True).first()
            if scan_lock.locked() else None
        )
        extra_context["stats"] = {
            "files": FileIndex.objects.count(),
            "last_scan": ScanTask.objects.filter(status="Completed").order_by("-id")
                         .values_list("created_at", flat=True).first(),
        }
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(BulkSearch)
class BulkSearchAdmin(admin.ModelAdmin):
    """Sidebar wala 'Bulk Number Search' page.

    Ye Step 1 / Scan History jaisa hi Django changelist page hai (isliye breadcrumb aur padding bilkul same).
    Table khali rakhte hain (queryset .none()), isliye koi DB query nahi chalti.
    """
    change_list_template = "admin/bulk_search.html"
    show_full_result_count = False

    def get_queryset(self, request):
        return super().get_queryset(request).none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update({
            "min_digits": MIN_DIGITS,
            "max_numbers": MAX_NUMBERS,
            "has_index": FileIndex.objects.exists(),   # kuch scan hua hai ya nahi
        })
        return super().changelist_view(request, extra_context=extra_context)

@admin.register(ScanTask)
class ScanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "folders_scanned", "files_indexed",
                    "files_skipped", "files_failed", "message", "created_at")
    ordering = ("-id",)


# ---- Sidebar ko workflow order me dikhao (Step 1 -> Step 2 -> History -> Users) ----
_default_get_app_list = admin.site.get_app_list


def _workflow_app_list(request, app_label=None):
    app_list = _default_get_app_list(request, app_label)
    return app_list if app_label else build_sidebar(app_list)   # kisi ek app ka page waisa hi rehne do


admin.site.get_app_list = _workflow_app_list