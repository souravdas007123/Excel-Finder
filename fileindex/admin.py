import re

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse

from .models import BulkSearch, FileIndex, NumberIndex, ScanTask
from .scanner import MIN_DIGITS


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
            (key, loc["label"]) for key, loc in settings.SCAN_LOCATIONS.items()
        ]
        return super().changelist_view(request, extra_context=extra_context)


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
        }
        return TemplateResponse(request, "admin/bulk_search.html", context)


@admin.register(ScanTask)
class ScanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "folders_scanned", "files_indexed",
                    "files_skipped", "files_failed", "message", "created_at")
    ordering = ("-id",)
