import re

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.utils.html import format_html, format_html_join
from django.template.response import TemplateResponse
from django.utils import timezone

from .drives import get_scan_locations
from .excel_parser import match_key
from .models import BulkSearch, FileIndex, NumberIndex, ScanTask
from .scanner import MAX_FAILURES_STORED, MIN_DIGITS, scan_lock
from .sidebar import build_sidebar
from .views import MAX_NUMBERS, custom_path_allowed


@admin.register(FileIndex)
class FileIndexAdmin(admin.ModelAdmin):
    list_display = ("file_name", "file_path", "file_size", "last_scanned")
    search_fields = ("file_name", "file_path")
    search_help_text = "Search by file name, path or number (matched on the last 10 digits). Separate several numbers with a comma or space."
    list_per_page = 50
    show_full_result_count = False   # badi table par extra COUNT query nahi chalegi
    change_list_template = "admin/file_index_changelist.html"

    def get_search_results(self, request, queryset, search_term):
        found, may_have_duplicates = super().get_search_results(request, queryset, search_term)

        terms = {re.sub(r"\D", "", t) for t in re.split(r"[,;\s]+", search_term)}
        terms.discard("")
        if terms:
            keys = {match_key(t) for t in terms}   # country code / leading 0 ignore (aakhri 10 digits)
            file_ids = NumberIndex.objects.filter(match_key__in=keys).values("file_id")
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
        last = ScanTask.objects.exclude(status="Running").order_by("-id").first()
        extra_context["last_scan_failed"] = (
            {"id": last.id, "count": last.files_failed} if last and last.files_failed else None
        )
        extra_context["stats"] = {
            "files": FileIndex.objects.count(),
            "last_scan": ScanTask.objects.filter(status="Completed").order_by("-id")
                         .values_list("created_at", flat=True).first(),
        }
        return super().changelist_view(request, extra_context=extra_context)


def _index_freshness():
    """Search page ke liye: index me kitni files hain, aakhri scan kab hua, aur kya wo purana ho gaya (SCAN_STALE_DAYS)."""
    last = (ScanTask.objects.filter(status="Completed").order_by("-id")
            .values_list("created_at", flat=True).first())
    stale_days = getattr(settings, "SCAN_STALE_DAYS", 7)
    return {
        "index_files": FileIndex.objects.count(),
        "last_scan": last,
        "stale_days": stale_days,
        "index_is_stale": bool(last and (timezone.now() - last).days >= stale_days),
    }


@admin.register(BulkSearch)
class BulkSearchAdmin(admin.ModelAdmin):
    """Sidebar wala 'Bulk Number Search' page. Koi DB query nahi chalti, sirf template render hota hai."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            "title": "Bulk Number Search",
            "opts": self.model._meta,
            "min_digits": MIN_DIGITS,
            "max_numbers": MAX_NUMBERS,
            "has_index": FileIndex.objects.exists(),   # kuch scan hua hai ya nahi
        }
        context.update(_index_freshness())
        return TemplateResponse(request, "admin/bulk_search.html", context)


@admin.register(ScanTask)
class ScanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "folders_scanned", "files_indexed",
                    "files_skipped", "files_failed", "message", "created_at")
    ordering = ("-id",)
    # History hai: sirf dekhne ke liye (edit / Save nahi). Delete admin se ho sakta hai.
    fields = ("status", "folders_scanned", "files_indexed", "files_skipped", "files_failed", "message",
              "created_at", "failed_files")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Files that could not be read")
    def failed_files(self, obj):
        rows = list(obj.failures.order_by("id")[:MAX_FAILURES_STORED])
        if not rows:
            return "None"
        table = format_html_join(
            "", '<tr><td style="word-break:break-all">{}</td><td>{}</td></tr>',
            ((r.file_path, r.reason) for r in rows))
        note = ""
        if obj.files_failed > len(rows):
            note = format_html("<p>Showing the first {} of {} files.</p>", len(rows), obj.files_failed)
        return format_html("<table><thead><tr><th>File</th><th>Why</th></tr></thead><tbody>{}</tbody></table>{}",
                           table, note)


# ---- Sidebar ko workflow order me dikhao (Step 1 -> Step 2 -> History -> Users) ----
_default_get_app_list = admin.site.get_app_list


def _workflow_app_list(request, app_label=None):
    app_list = _default_get_app_list(request, app_label)
    return app_list if app_label else build_sidebar(app_list)   # kisi ek app ka page waisa hi rehne do


admin.site.get_app_list = _workflow_app_list