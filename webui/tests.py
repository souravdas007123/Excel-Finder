"""Naya UI (/app/) ke tests.  Chalane ke liye:  python manage.py test webui"""
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from fileindex import services as core
from fileindex.excel_parser import match_key
from fileindex.models import FileIndex, NumberIndex, ScanFailure, ScanTask
from fileindex.scanner import scan_lock, scan_progress
from licensing import protocol, service
from licensing.models import LicenseState
from search import middleware as first_run
from search import update_check

from . import helpers, search_views
from .templatetags import webui_tags

HX = {"HTTP_HX_REQUEST": "true"}
GOOD_KEY = "EXFN-ABCDE-FGHJK-LMNPQ-RSTUV"


def hx(target=None, **extra):
    headers = dict(HX)
    if target:
        headers["HTTP_HX_TARGET"] = target
    headers.update(extra)
    return headers


class UiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser("admin", "a@example.com", "pw-12345-xyz")
        cls.staff = User.objects.create_user("staff", "s@example.com", "pw-12345-xyz", is_staff=True)
        cls.outsider = User.objects.create_user("outsider", "o@example.com", "pw-12345-xyz")

    def setUp(self):
        first_run.reset_first_run_cache()
        cache.clear()
        if scan_lock.locked():              # kisi purane test ka atka hua lock (warna test hamesha ke liye ruk jata)
            scan_lock.release()
        scan_progress.update(run=None, task_id=None, total=None, counted=0)      # purane scan ki ginti
        self.client.force_login(self.admin)

    def add_file(self, name="sales.xlsx", folder="/data/Sales", numbers=(), sheet="Sheet1", mtime=1_700_000_000):
        path = f"{folder}/{name}"
        f = FileIndex.objects.create(file_name=name, file_path=path, file_size=2048, file_mtime=mtime)
        NumberIndex.objects.bulk_create([
            NumberIndex(file=f, number=n, match_key=match_key(n), sheet=sheet, row=i + 2, col=2) for i, n in enumerate(numbers)])
        return f


# ------------------------------------------------------------------ login
class LoginTests(UiTestCase):
    def setUp(self):
        super().setUp()
        self.client.logout()

    def test_root_and_pages_go_to_login_when_signed_out(self):
        for url in ("/", "/app/", "/app/scan/", "/app/search/", "/app/files/", "/app/history/", "/app/license/"):
            r = self.client.get(url, follow=False)
            self.assertEqual(r.status_code, 302, url)
        r = self.client.get("/app/scan/")
        self.assertEqual(r["Location"], "/app/login/?next=/app/scan/")

    def test_htmx_requests_get_a_redirect_header_not_a_page(self):
        r = self.client.post("/app/search/run/", {"numbers": "9856325417"}, **HX)
        self.assertEqual((r.status_code, r["HX-Redirect"].split("?")[0]), (204, "/app/login/"))

    def test_login_works_and_goes_to_next(self):
        page = self.client.get("/app/login/")
        self.assertContains(page, "Welcome back")
        r = self.client.post("/app/login/", {"username": "admin", "password": "pw-12345-xyz", "next": "/app/files/"})
        self.assertRedirects(r, "/app/files/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_external_next_is_ignored(self):
        r = self.client.post("/app/login/", {"username": "admin", "password": "pw-12345-xyz", "next": "https://evil.example/x"})
        self.assertRedirects(r, "/app/", fetch_redirect_response=False)

    def test_wrong_password_and_non_staff_are_refused(self):
        r = self.client.post("/app/login/", {"username": "admin", "password": "nope"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "correct username and password")
        r = self.client.post("/app/login/", {"username": "outsider", "password": "pw-12345-xyz"})
        self.assertContains(r, "cannot use the app")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_needs_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/app/logout/").status_code, 405)
        r = self.client.post("/app/logout/")
        self.assertRedirects(r, "/app/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/app/").status_code, 302)

    def test_non_staff_cannot_open_pages(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get("/app/").status_code, 302)

    def test_signed_in_user_skips_the_login_page(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get("/app/login/"), "/app/", fetch_redirect_response=False)


# ------------------------------------------------------------------ dashboard / files / history
class DashboardTests(UiTestCase):
    def test_empty_dashboard_invites_the_first_scan(self):
        r = self.client.get("/app/")
        self.assertContains(r, "Start your first scan")
        self.assertContains(r, "htmx.min.js")
        self.assertContains(r, 'hx-get="/app/dashboard/stats/"')

    def test_stats_partial_has_the_numbers(self):
        self.add_file(numbers=["9856325417", "9856325418"])
        ScanTask.objects.create(status="Completed", files_indexed=1)
        r = self.client.get("/app/dashboard/stats/")
        self.assertContains(r, "Files indexed")
        self.assertEqual(r.context["files"], 1)
        self.assertEqual(r.context["numbers"], 2)

    def test_stale_index_is_flagged(self):
        from datetime import timedelta
        from django.utils import timezone
        task = ScanTask.objects.create(status="Completed")
        ScanTask.objects.filter(pk=task.pk).update(created_at=timezone.now() - timedelta(days=30))
        r = self.client.get("/app/dashboard/stats/")
        self.assertTrue(r.context["stale"])
        self.assertContains(r, "Older than 7 days")

    def test_unreadable_files_card_warns(self):
        ScanTask.objects.create(status="Completed", files_failed=3)
        self.assertContains(self.client.get("/app/dashboard/stats/"), "See which files and why")

    def test_running_scan_banner(self):
        ScanTask.objects.create(status="Running", files_indexed=7)
        self.assertTrue(scan_lock.acquire(blocking=False))
        self.addCleanup(scan_lock.release)
        self.assertContains(self.client.get("/app/"), "A scan is running")

    def test_recent_scans_listed(self):
        ScanTask.objects.create(status="Completed", files_indexed=12)
        self.assertContains(self.client.get("/app/"), "Completed")

    def test_sidebar_has_the_license_chip_and_no_admin_link(self):
        r = self.client.get("/app/")
        self.assertContains(r, "Development copy")
        self.assertNotContains(r, "/admin/")
        self.assertNotContains(r, "Manage users")

    def test_the_customer_app_has_no_django_admin(self):
        for url in ("/admin/", "/admin/login/", "/admin/auth/user/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)


class FilesPageTests(UiTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.add_file("alpha.xlsx", "/data/A", ["9856325417"])
        self.b = self.add_file("beta.xlsx", "/data/B", ["8569741235"])

    def test_lists_all_files(self):
        r = self.client.get("/app/files/")
        self.assertContains(r, "alpha.xlsx")
        self.assertContains(r, "beta.xlsx")
        self.assertContains(r, "2 files")

    def test_search_by_name_path_and_number(self):
        self.assertContains(self.client.get("/app/files/?q=alp"), "alpha.xlsx")
        self.assertNotContains(self.client.get("/app/files/?q=alp"), "beta.xlsx")
        self.assertContains(self.client.get("/app/files/?q=/data/B"), "beta.xlsx")
        r = self.client.get("/app/files/?q=919856325417")        # country code ke saath, aakhri 10 digits se match
        self.assertContains(r, "alpha.xlsx")
        self.assertNotContains(r, "beta.xlsx")

    def test_htmx_target_returns_only_the_table(self):
        r = self.client.get("/app/files/?q=alp", **hx("files-table"))
        self.assertContains(r, 'id="files-table"')
        self.assertNotContains(r, "<html")
        self.assertContains(self.client.get("/app/files/"), "<html")

    def test_pagination(self):
        for i in range(30):
            self.add_file(f"bulk{i:02d}.xlsx", "/data/Bulk")
        r = self.client.get("/app/files/")
        self.assertEqual(len(r.context["page"].object_list), 25)
        self.assertContains(r, "page=2")
        self.assertEqual(len(self.client.get("/app/files/?page=2").context["page"].object_list), 7)

    def test_empty_states(self):
        self.assertContains(self.client.get("/app/files/?q=zzzz"), "No files match")
        FileIndex.objects.all().delete()
        self.assertContains(self.client.get("/app/files/"), "No files indexed yet")

    def test_search_text_is_escaped(self):
        self.assertNotContains(self.client.get('/app/files/?q=<script>alert(1)</script>'), "<script>alert(1)</script>")


class HistoryTests(UiTestCase):
    def test_list_and_failure_detail(self):
        task = ScanTask.objects.create(status="Completed", files_indexed=5, files_failed=2)
        ScanFailure.objects.create(task=task, file_path="/data/bad.xlsx", reason="Password-protected file")
        r = self.client.get("/app/history/")
        self.assertContains(r, f"#{task.id}")
        self.assertContains(r, "Details")
        d = self.client.get(f"/app/history/{task.id}/")
        self.assertContains(d, "/data/bad.xlsx")
        self.assertContains(d, "Password-protected file")

    def test_unknown_scan_is_404_and_error_message_shown(self):
        self.assertEqual(self.client.get("/app/history/999/").status_code, 404)
        task = ScanTask.objects.create(status="Error", message="Disk is full")
        self.assertContains(self.client.get(f"/app/history/{task.id}/"), "Disk is full")

    def test_empty(self):
        self.assertContains(self.client.get("/app/history/"), "No scans yet")

    def test_partial(self):
        ScanTask.objects.create(status="Completed")
        r = self.client.get("/app/history/", **hx("history-table"))
        self.assertNotContains(r, "<html")


# ------------------------------------------------------------------ scan
class ScanTests(UiTestCase):
    def test_scan_page(self):
        r = self.client.get("/app/scan/")
        self.assertContains(r, "Choose what to scan")
        self.assertContains(r, "Specific folder")
        self.assertContains(r, 'id="scan-live"')

    def test_staff_without_superuser_has_no_custom_folder_or_clear(self):
        self.client.force_login(self.staff)
        r = self.client.get("/app/scan/")
        self.assertNotContains(r, "Specific folder")
        self.assertNotContains(r, "Clear file index")

    def test_start_with_a_bad_location_is_a_toast_and_changes_nothing(self):
        r = self.client.post("/app/scan/start/", {"location": "nope"}, **HX)
        self.assertEqual(r["HX-Reswap"], "none")
        self.assertContains(r, "Invalid location")
        self.assertFalse(ScanTask.objects.exists())

    def test_start_with_a_missing_folder(self):
        r = self.client.post("/app/scan/start/", {"location": "__custom__", "custom_path": "/no/such/folder"}, **HX)
        self.assertContains(r, "Folder not found")

    def test_custom_folder_needs_a_superuser(self):
        self.client.force_login(self.staff)
        r = self.client.post("/app/scan/start/", {"location": "__custom__", "custom_path": "/tmp"}, **HX)
        self.assertContains(r, "not allowed")

    def test_start_returns_a_self_polling_progress_card(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(core.threading, "Thread") as thread:
            r = self.client.post("/app/scan/start/", {"location": "__custom__", "custom_path": folder}, **HX)
            task = ScanTask.objects.get()
            self.assertContains(r, f'hx-get="/app/scan/status/?task={task.id}"')
            self.assertContains(r, 'hx-trigger="every 1s"')
            self.assertContains(r, "Stop scan")
            self.assertContains(r, 'id="scan-start"')                       # button disabled (out-of-band)
            thread.return_value.start.assert_called_once()
            scan_lock.release()

    def test_status_of_a_running_scan_keeps_polling(self):
        task = ScanTask.objects.create(status="Running", files_indexed=4, files_skipped=1)
        r = self.client.get(f"/app/scan/status/?task={task.id}", **HX)
        self.assertContains(r, 'hx-trigger="every 1s"')
        self.assertContains(r, "4 indexed")
        self.assertNotIn("HX-Trigger", r)

    def test_finished_scan_stops_polling_and_refreshes_stats(self):
        task = ScanTask.objects.create(status="Completed", files_indexed=9, files_failed=1)
        r = self.client.get(f"/app/scan/status/?task={task.id}", **HX)
        self.assertNotContains(r, 'hx-trigger="every 1s"')
        self.assertContains(r, "Scan complete")
        self.assertEqual(r["HX-Trigger"], "indexChanged")
        self.assertContains(r, "Search numbers")

    def test_stopped_and_failed_scans_say_so(self):
        stopped = ScanTask.objects.create(status="Cancelled")
        failed = ScanTask.objects.create(status="Error", message="Boom")
        self.assertContains(self.client.get(f"/app/scan/status/?task={stopped.id}"), "Scan stopped")
        r = self.client.get(f"/app/scan/status/?task={failed.id}")
        self.assertContains(r, "Scan failed")
        self.assertContains(r, "Boom")

    def test_status_with_a_bad_task_id_is_empty(self):
        self.assertEqual(self.client.get("/app/scan/status/?task=abc").content, b"")
        self.assertEqual(self.client.get("/app/scan/status/?task=99999").content, b"")

    def test_counting_phase_shows_an_indeterminate_bar(self):
        task = ScanTask.objects.create(status="Running")
        r = self.client.get(f"/app/scan/status/?task={task.id}")
        self.assertContains(r, "progress indeterminate")
        self.assertContains(r, "Counting files")

    def test_eta_text(self):
        bar = helpers.scan_progress_view({"files_indexed": 50, "files_skipped": 0, "files_failed": 0, "files_total": 200,
                                          "files_counted": 200, "elapsed": 100})
        self.assertEqual(bar["percent"], 25.0)
        self.assertIn("50 of 200 files", bar["text"])
        self.assertIn("left", bar["text"])
        bar = helpers.scan_progress_view({"files_indexed": 5, "files_skipped": 0, "files_failed": 0, "files_total": 3,
                                          "files_counted": 3, "elapsed": 1})
        self.assertEqual(bar["percent"], 100.0)                 # bar 100% se upar nahi

    def test_eta_and_clock_formats(self):
        self.assertEqual(helpers.fmt_eta(10), "less than a minute")
        self.assertEqual(helpers.fmt_eta(600), "~10 min")
        self.assertEqual(helpers.fmt_eta(5400), "~1 h 30 min")

    def test_stop_without_a_running_scan_is_a_toast(self):
        r = self.client.post("/app/scan/stop/", **HX)
        self.assertContains(r, "No scan is running")
        self.assertEqual(r["HX-Reswap"], "none")

    def test_stop_signals_the_scanner(self):
        task = ScanTask.objects.create(status="Running")
        from fileindex.scanner import cancel_event
        cancel_event.clear()
        self.addCleanup(cancel_event.clear)
        self.assertTrue(scan_lock.acquire(blocking=False))
        self.addCleanup(scan_lock.release)
        r = self.client.post("/app/scan/stop/", **HX)
        self.assertTrue(cancel_event.is_set())
        self.assertContains(r, "Stopping")
        self.assertEqual(task.status, "Running")

    def test_actions_need_post(self):
        for url in ("/app/scan/start/", "/app/scan/stop/"):
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_folder_browser(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "Sales").mkdir()
            (Path(root) / ".hidden").mkdir()
            r = self.client.get("/app/scan/browse/", {"path": root})
            self.assertContains(r, "Sales")
            self.assertNotContains(r, ".hidden")
            self.assertContains(r, "Use this folder")
            self.assertContains(self.client.get("/app/scan/browse/", {"path": root + "/missing"}), "Folder not found")

    def test_folder_browser_is_superuser_only(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/app/scan/browse/", {"path": "/tmp"}), "not allowed")

    def test_clear_index_asks_first_then_clears(self):
        self.add_file(numbers=["9856325417"])
        ask = self.client.get("/app/scan/clear/", **HX)
        self.assertContains(ask, "Clear the file index?")
        self.assertEqual(FileIndex.objects.count(), 1)
        r = self.client.post("/app/scan/clear/", **HX)
        self.assertEqual(r.status_code, 204)
        self.assertEqual((FileIndex.objects.count(), NumberIndex.objects.count()), (0, 0))
        events = json.loads(r["HX-Trigger"])
        self.assertTrue(events["indexChanged"] and events["closeModal"])
        self.assertIn("1 files removed", events["toast"]["message"])

    def test_clear_index_needs_permission_and_no_running_scan(self):
        self.add_file()
        self.client.force_login(self.staff)
        self.assertContains(self.client.post("/app/scan/clear/", **HX), "permission")
        self.assertEqual(FileIndex.objects.count(), 1)
        self.client.force_login(self.admin)
        self.assertTrue(scan_lock.acquire(blocking=False))
        try:
            self.assertContains(self.client.post("/app/scan/clear/", **HX), "scan is running")
        finally:
            scan_lock.release()
        self.assertEqual(FileIndex.objects.count(), 1)


# ------------------------------------------------------------------ search
class SearchTests(UiTestCase):
    NUMS = ["9856325417", "8569741235", "7000000001"]

    def setUp(self):
        super().setUp()
        self.f1 = self.add_file("sales.xlsx", "/data/Sales", ["9856325417", "8569741235"], mtime=1_700_000_000)
        self.f2 = self.add_file("leads.xlsx", "/data/Leads", ["9856325417"], sheet="Leads", mtime=1_800_000_000)

    def run_search(self, text="9856325417\n8569741235\n7000000001\n", **extra):
        return self.client.post("/app/search/run/", {"numbers": text, "last10": "1", **extra}, **hx("results"))

    def token(self, r):
        return r.context["token"]

    def test_page(self):
        r = self.client.get("/app/search/")
        self.assertContains(r, "Search database")
        self.assertContains(r, 'id="numbers"')
        self.assertContains(r, "Upload Excel / CSV")

    def test_page_without_index_says_scan_first(self):
        FileIndex.objects.all().delete()
        r = self.client.get("/app/search/")
        self.assertContains(r, "Nothing to search yet")

    def test_results_summary_and_by_file_tab(self):
        r = self.run_search()
        self.assertEqual(r.status_code, 200)
        s = r.context["s"]
        self.assertEqual((s["searched"], s["found"], s["not_found"], s["files"]), (3, 2, 1, 2))
        self.assertContains(r, "sales.xlsx")
        self.assertContains(r, "leads.xlsx")
        self.assertContains(r, "Export Excel report")
        self.assertContains(r, "By file")
        self.assertContains(r, "By number")

    def test_country_code_and_leading_zero_match(self):
        r = self.run_search("+91 98563 25417\n09856325417")
        self.assertEqual(r.context["s"]["found"], 1)             # dono ek hi number maane gaye
        self.assertEqual(r.context["s"]["duplicates"], 1)

    def test_exact_mode(self):
        r = self.client.post("/app/search/run/", {"numbers": "919856325417", "last10": "0"}, **hx("results"))
        self.assertEqual(r.context["s"]["found"], 0)

    def test_empty_and_too_short_input_are_toasts(self):
        r = self.client.post("/app/search/run/", {"numbers": "  "}, **HX)
        self.assertContains(r, "Paste some phone numbers")
        self.assertEqual(r["HX-Reswap"], "none")
        r = self.client.post("/app/search/run/", {"numbers": "123, 45"}, **HX)
        self.assertContains(r, "No valid numbers")

    def test_by_number_tab_and_filters(self):
        t = self.token(self.run_search())
        r = self.client.get(f"/app/search/{t}/numbers/", **hx("results-pane"))
        self.assertContains(r, "9856325417")
        self.assertContains(r, "2 matches in 2 files")
        self.assertContains(r, "Not found")
        found = self.client.get(f"/app/search/{t}/numbers/?filter=found", **hx("results-pane"))
        self.assertEqual(found.context["total"], 2)
        missing = self.client.get(f"/app/search/{t}/numbers/?filter=missing", **hx("results-pane"))
        self.assertEqual([x["number"] for x in missing.context["items"]], ["7000000001"])
        multi = self.client.get(f"/app/search/{t}/numbers/?filter=multi", **hx("results-pane"))
        self.assertEqual([x["number"] for x in multi.context["items"]], ["9856325417"])
        typed = self.client.get(f"/app/search/{t}/numbers/?q=8569", **hx("numbers-list"))
        self.assertEqual(typed.context["total"], 1)
        self.assertNotContains(typed, "<html")
        self.assertNotContains(typed, 'class="toolbar"')          # sirf list, filter bar nahi

    def test_each_hit_shows_where_and_has_view_row_and_copy_path(self):
        t = self.token(self.run_search())
        r = self.client.get(f"/app/search/{t}/numbers/", **hx("results-pane"))
        self.assertContains(r, "Sheet <b>Sheet1</b> · row <b>2</b> · column <b>B</b>")
        self.assertContains(r, "View row")
        self.assertContains(r, 'data-copy="/data/Sales/sales.xlsx"')

    def test_windows_paths_are_joined_with_backslashes(self):
        self.assertEqual(search_views.full_path("D:\\Sales", "a.xlsx"), "D:\\Sales\\a.xlsx")
        self.assertEqual(search_views.full_path("D:\\Sales\\", "a.xlsx"), "D:\\Sales\\a.xlsx")
        self.assertEqual(search_views.full_path("/data", "a.xlsx"), "/data/a.xlsx")
        self.assertEqual(search_views.full_path("", "a.xlsx"), "a.xlsx")

    def test_files_tab_filter_and_sort(self):
        t = self.token(self.run_search())
        r = self.client.get(f"/app/search/{t}/files/", **hx("results-pane"))
        self.assertContains(r, "Filter by file")
        sorted_name = self.client.get(f"/app/search/{t}/files/?sort=name", **hx("files-list"))
        self.assertEqual([f["file"] for f in sorted_name.context["items"]], ["leads.xlsx", "sales.xlsx"])
        newest = self.client.get(f"/app/search/{t}/files/?sort=newest", **hx("files-list"))
        self.assertEqual(newest.context["items"][0]["file"], "leads.xlsx")
        most = self.client.get(f"/app/search/{t}/files/", **hx("files-list"))
        self.assertEqual(most.context["items"][0]["file"], "sales.xlsx")       # 2 numbers > 1
        filtered = self.client.get(f"/app/search/{t}/files/?q=lea", **hx("files-list"))
        self.assertEqual(filtered.context["total"], 1)
        self.assertContains(self.client.get(f"/app/search/{t}/files/?q=zzz", **hx("files-list")), "No files match")

    def test_not_found_tab_has_copy_and_download(self):
        t = self.token(self.run_search())
        r = self.client.get(f"/app/search/{t}/missing/", **hx("results-pane"))
        self.assertContains(r, "7000000001")
        self.assertContains(r, "Download .txt")
        self.assertContains(r, "Copy all")

    def test_everything_found_message(self):
        t = self.token(self.run_search("9856325417"))
        self.assertContains(self.client.get(f"/app/search/{t}/missing/", **hx("results-pane")), "Every number was found")

    def test_all_missing_starts_on_the_not_found_tab(self):
        r = self.run_search("7000000009")
        self.assertEqual(r.context["tab"], "missing")
        self.assertContains(r, "7000000009")

    def test_load_more_pages_for_numbers(self):
        many = [f"70000{i:05d}" for i in range(120)]
        t = self.token(self.run_search("\n".join(many)))
        first = self.client.get(f"/app/search/{t}/numbers/", **hx("results-pane"))
        self.assertEqual(len(first.context["items"]), 50)
        self.assertContains(first, "page=2")
        second = self.client.get(f"/app/search/{t}/numbers/?page=2", **hx("this"))
        self.assertEqual(len(second.context["items"]), 50)
        self.assertNotContains(second, 'class="toolbar"')
        third = self.client.get(f"/app/search/{t}/numbers/?page=3", **hx("this"))
        self.assertEqual(len(third.context["items"]), 20)
        self.assertNotContains(third, "page=4")

    def test_load_more_pages_for_files(self):
        for i in range(70):
            self.add_file(f"many{i:02d}.xlsx", "/data/Many", ["7000000001"])
        t = self.token(self.run_search("7000000001"))
        first = self.client.get(f"/app/search/{t}/files/", **hx("results-pane"))
        self.assertEqual(len(first.context["items"]), 50)
        second = self.client.get(f"/app/search/{t}/files/?page=2", **hx("this"))
        self.assertEqual(len(second.context["items"]), 20)

    def test_file_details_list_locations_with_view_row(self):
        t = self.token(self.run_search())
        r = self.client.get(f"/app/search/{t}/files/{self.f1.id}/", **HX)
        self.assertContains(r, "9856325417")
        self.assertContains(r, "Sheet1 · row 2 · col B")
        self.assertContains(r, f"file_id={self.f1.id}")

    def test_file_details_of_an_unknown_file_is_404(self):
        t = self.token(self.run_search())
        self.assertEqual(self.client.get(f"/app/search/{t}/files/99999/", **HX).status_code, 404)

    def test_expired_token_and_other_users_token(self):
        t = self.token(self.run_search())
        cache.clear()
        self.assertContains(self.client.get(f"/app/search/{t}/files/", **hx("results-pane")), "have expired")
        t = self.token(self.run_search())
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(f"/app/search/{t}/files/", **hx("results-pane")), "have expired")
        self.assertContains(self.client.get(f"/app/search/{t}/files/{self.f1.id}/", **HX), "have expired")

    def test_unknown_tab_is_404(self):
        t = self.token(self.run_search())
        self.assertEqual(self.client.get(f"/app/search/{t}/bogus/").status_code, 404)

    def test_numbers_tab_hidden_for_huge_lists(self):
        with mock.patch.object(core, "DETAIL_LIMIT", 2):
            r = self.run_search("\n".join(f"70000{i:05d}" for i in range(5)))
        self.assertNotContains(r, "By number")
        self.assertContains(r, "per-number details are hidden")

    def test_notes_for_duplicates_and_skipped(self):
        r = self.run_search("9856325417\n9856325417\n12")
        self.assertContains(r, "1 duplicate number removed")
        self.assertContains(r, "skipped")

    def test_xss_in_file_names_is_escaped(self):
        self.add_file("<img src=x onerror=alert(1)>.xlsx", "/data/X", ["7000000001"])
        t = self.token(self.run_search("7000000001"))
        r = self.client.get(f"/app/search/{t}/numbers/", **hx("results-pane"))
        self.assertNotContains(r, "<img src=x")

    def test_search_counts_as_usage(self):
        with mock.patch.object(core, "record_usage") as usage:
            self.run_search()
        usage.assert_called_with("search")

    def test_row_detail_modal(self):
        with mock.patch.object(core, "read_row", return_value=(["Name", "Phone"], ["Ravi", "9856325417"])):
            r = self.client.get("/app/search/row/", {"file_id": self.f1.id, "sheet": "Sheet1", "row": 2, "col": 2}, **HX)
        self.assertContains(r, "Ravi")
        self.assertContains(r, "hit-cell")
        self.assertContains(r, "sales.xlsx")

    def test_row_detail_errors(self):
        r = self.client.get("/app/search/row/", {"file_id": 99999, "sheet": "S", "row": 2}, **HX)
        self.assertContains(r, "File not in index")
        with mock.patch.object(core, "read_row", side_effect=FileNotFoundError):
            r = self.client.get("/app/search/row/", {"file_id": self.f1.id, "sheet": "S", "row": 2}, **HX)
        self.assertContains(r, "File moved or deleted")
        r = self.client.get("/app/search/row/", {"file_id": "x"}, **HX)
        self.assertContains(r, "Invalid request")

    def upload(self, name, content, **extra):
        return self.client.post("/app/search/extract/", {"file": SimpleUploadedFile(name, content), **extra}, **HX)

    def test_extract_numbers_from_a_csv(self):
        r = self.upload("list.csv", b"name,phone\nRavi,9856325417\nAmit,8569741235\nRavi2,9856325417\n")
        self.assertContains(r, "9856325417")
        self.assertContains(r, "Loaded 2 numbers")
        self.assertContains(r, "1 duplicates removed")
        self.assertContains(r, 'id="numbers"')

    def test_extract_from_xlsx(self):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.append(["Phone"]); ws.append(["9856325417"]); ws.append(["8569741235"])
        buf = io.BytesIO(); wb.save(buf)
        r = self.upload("list.xlsx", buf.getvalue())
        self.assertContains(r, "8569741235")

    def test_extract_errors_are_toasts(self):
        self.assertContains(self.upload("a.pdf", b"x"), "Unsupported file")
        self.assertContains(self.upload("a.csv", b"nothing here\n"), "No phone numbers found")
        r = self.client.post("/app/search/extract/", {}, **HX)
        self.assertContains(r, "No file received")
        self.assertEqual(r["HX-Reswap"], "none")

    def test_big_file_offers_the_full_report(self):
        with mock.patch.object(core, "MAX_NUMBERS", 3):
            r = self.upload("big.csv", ("\n".join(f"70000{i:05d}" for i in range(10))).encode())
        self.assertContains(r, "Full Excel report")
        self.assertContains(r, "/app/search/report/")

    def test_search_page_posts_exports_to_the_app_endpoints(self):
        r = self.client.get("/app/search/")
        self.assertContains(r, 'action="/app/search/export/"')

    def test_export_downloads_an_excel_report(self):
        import openpyxl
        r = self.client.post("/app/search/export/", {"numbers": "9856325417\n7000000001", "last10": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("attachment; filename=\"number_search_report_", r["Content-Disposition"])
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Summary", "By File", "Found", "Not Found"])

    def test_full_report_for_an_uploaded_file(self):
        import openpyxl
        up = SimpleUploadedFile("nums.csv", b"9856325417\n7000000001\n")
        r = self.client.post("/app/search/report/", {"file": up})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(list(openpyxl.load_workbook(io.BytesIO(r.content))["Found"].iter_rows(min_row=2))), 2)    # 2 files me hai
        r = self.client.post("/app/search/report/", {"file": SimpleUploadedFile("x.txt", b"no digits")})
        self.assertEqual((r.status_code, r.content.decode()), (400, "No numbers found in the file"))
        r = self.client.post("/app/search/report/", {})
        self.assertEqual(r.status_code, 400)

    def test_downloads_need_a_signed_in_staff_user_and_post(self):
        self.assertEqual(self.client.get("/app/search/export/").status_code, 405)
        for user in (None, self.outsider):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in ("/app/search/export/", "/app/search/report/"):
                r = self.client.post(url, {"numbers": "9856325417"})
                self.assertEqual(r.status_code, 302, url)
                self.assertTrue(r["Location"].startswith("/app/login/"), url)


# ------------------------------------------------------------------ license page
class LicensePageTests(UiTestCase):
    ENFORCED = dict(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY="x", LICENSE_SERVER_URL="https://license.example.test")

    def test_not_enforced_page(self):
        r = self.client.get("/app/license/")
        self.assertContains(r, "License checks are switched off")
        self.assertContains(r, "Check for updates")
        self.assertNotContains(r, "Activate your license")             # development copy me form ka koi kaam nahi

    @override_settings(**ENFORCED)
    def test_without_a_key_the_page_asks_for_it_and_says_how_to_get_one(self):
        service.invalidate()
        LicenseState.objects.update_or_create(pk=1, defaults=dict(account_name="Ravi Kumar", account_email="ravi@example.com"))
        with override_settings(LICENSE_SUPPORT="help@example.com"):
            r = self.client.get("/app/license/")
        self.assertContains(r, "Key needed")
        self.assertContains(r, "Activate your license")
        self.assertContains(r, "Don't have a key yet?")
        self.assertContains(r, "help@example.com")
        self.assertContains(r, "Ravi Kumar &lt;ravi@example.com&gt;")
        self.assertNotContains(r, "Check now")                           # abhi koi license hi nahi
        self.assertNotContains(r, "Create an account")
        self.assertNotContains(r, "Sign in")

    @override_settings(**ENFORCED)
    def test_active_license_shows_details_and_hides_the_key_form(self):
        with mock.patch.object(service, "current_status", return_value=protocol.LicenseStatus(
                "active", True, "License is active.", license_type="yearly", customer="Ravi Kumar", days_left=200)):
            LicenseState.objects.update_or_create(pk=1, defaults=dict(license_key=GOOD_KEY, token="t"))
            r = self.client.get("/app/license/")
        self.assertContains(r, "Active")
        self.assertContains(r, "Yearly plan")
        self.assertContains(r, "200 days left")
        self.assertContains(r, "Check now")
        self.assertContains(r, "Move this license to another PC")
        self.assertNotContains(r, "Activate your license")
        self.assertNotContains(r, GOOD_KEY)                              # puri key kabhi nahi dikhti

    @override_settings(**ENFORCED)
    def test_cancelled_license_says_so_and_still_lets_you_enter_a_new_key(self):
        LicenseState.objects.update_or_create(pk=1, defaults=dict(
            license_key=GOOD_KEY, token="t", blocked_code="revoked", blocked_message="Cancelled: refund."))
        service.invalidate()
        r = self.client.get("/app/license/")
        self.assertContains(r, "Cancelled")
        self.assertContains(r, "refund")
        self.assertContains(r, "Activate your license")
        self.assertContains(r, "Enter your current key")

    @override_settings(**ENFORCED)
    def test_staff_who_is_not_an_admin_sees_no_forms(self):
        service.invalidate()
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get("/app/license/"), "Activate your license")

    def test_actions_are_superuser_only(self):
        self.client.force_login(self.staff)
        for name in ("check", "activate", "deactivate"):
            with mock.patch.object(service, "activate") as act, mock.patch.object(service, "deactivate") as deact:
                r = self.client.post(f"/app/license/{name}/", {"key": GOOD_KEY}, **HX)
            self.assertContains(r, "Only an administrator", msg_prefix=name)
            act.assert_not_called(); deact.assert_not_called()

    def test_removed_account_endpoints_are_gone(self):
        for name in ("register", "login", "signout", "panel"):
            self.assertEqual(self.client.post(f"/app/license/{name}/", {}, **HX).status_code, 404, name)

    def test_activating_a_key_opens_the_app(self):
        ok = service.Result(True, "License activated: Yearly plan.")
        with mock.patch.object(service, "activate", return_value=ok) as act, \
                mock.patch.object(service, "current_status", return_value=protocol.LicenseStatus("active", True, "ok")), \
                override_settings(**self.ENFORCED):
            r = self.client.post("/app/license/activate/", {"key": GOOD_KEY}, **HX)
        act.assert_called_once_with(GOOD_KEY)
        self.assertEqual((r.status_code, r["HX-Redirect"]), (204, "/app/"))
        self.assertIn("License activated", json.loads(r["HX-Trigger"])["toast"]["message"])

    @override_settings(**ENFORCED)
    def test_a_wrong_key_stays_on_the_page_with_the_reason(self):
        service.invalidate()
        bad = service.Result(False, "This license key is not valid. Please check it and try again.")
        with mock.patch.object(service, "activate", return_value=bad):
            r = self.client.post("/app/license/activate/", {"key": "EXFN-AAAAA-AAAAA-AAAAA-AAAAA"}, **HX)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "not valid")
        self.assertContains(r, "toast-bad")
        self.assertContains(r, 'id="license-panel"')
        self.assertNotIn("HX-Redirect", r)

    def test_check_and_deactivate_use_the_service(self):
        with mock.patch.object(service, "check_now", return_value=service.Result(False, "Could not reach the license server.")):
            r = self.client.post("/app/license/check/", **HX)
        self.assertContains(r, "Could not reach")
        self.assertContains(r, "toast-bad")
        with mock.patch.object(service, "deactivate", return_value=service.Result(True, "This PC has been deactivated.")) as out:
            r = self.client.post("/app/license/deactivate/", **HX)
        out.assert_called_once_with()
        self.assertContains(r, "deactivated")

    def test_update_check_and_dismiss_swap_the_banner(self):
        with override_settings(UPDATE_CHECK_URL=""):
            r = self.client.post("/app/updates/check/", **HX)
        self.assertContains(r, "not set up")
        notice = {"version": "1.2.0", "download_url": "https://example.com/d", "notes": ["Faster"], "current": "1.0.0",
                  "critical": False, "one_click": True, "installing": False}
        with mock.patch.object(update_check, "enabled", return_value=True), \
                mock.patch.object(update_check, "check_now", return_value=update_check.Result(True, "Version 1.2.0 is available")), \
                mock.patch("search.context_processors.update_check.current_notice", return_value=notice):
            r = self.client.post("/app/updates/check/", **HX)
        self.assertContains(r, 'id="update-banner"')
        self.assertContains(r, 'hx-swap-oob="true"')
        self.assertContains(r, "Update now")
        self.assertContains(r, "Faster")
        with mock.patch.object(update_check, "dismiss") as dismiss:
            r = self.client.post("/app/updates/dismiss/", {"version": "1.2.0"}, **HX)
        dismiss.assert_called_once_with("1.2.0")
        self.assertContains(r, 'id="update-banner"')

    def test_banner_is_on_every_page_when_an_update_exists(self):
        notice = {"version": "1.2.0", "download_url": "https://example.com/d", "notes": [], "current": "1.0.0",
                  "critical": True, "one_click": False, "installing": False}
        with mock.patch("search.context_processors.update_check.current_notice", return_value=notice):
            r = self.client.get("/app/")
        self.assertContains(r, "Excel Finder 1.2.0 is available")
        self.assertContains(r, "This update is required")
        self.assertNotContains(r, "Not now")


# ------------------------------------------------------------------ password bhool gaye (reset code)
@override_settings(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY="x", LICENSE_SERVER_URL="https://license.example.test")
class ForgotPasswordTests(UiTestCase):
    def setUp(self):
        super().setUp()
        self.client.logout()
        self.owner = get_user_model().objects.create_superuser("ravi@example.com", "ravi@example.com", "Old-Pass-12345")
        service.invalidate()

    DATA = {"email": "ravi@example.com", "code": "ABCD-EFGH", "password": "Brand-New-Pass-77", "confirm": "Brand-New-Pass-77"}

    def reset(self, **change):
        return self.client.post("/app/forgot/", {**self.DATA, **change})

    def test_page_explains_the_steps_and_login_links_to_it(self):
        r = self.client.get("/app/forgot/")
        self.assertContains(r, "Reset your password")
        self.assertContains(r, "Email the seller")
        self.assertContains(r, 'name="code"')
        self.assertContains(self.client.get("/app/login/"), 'href="/app/forgot/"')

    def test_page_is_open_without_a_license_and_without_signing_in(self):
        self.assertEqual(self.client.get("/app/forgot/").status_code, 200)       # license band hone par bhi

    def test_right_code_sets_the_new_password_on_this_pc(self):
        with mock.patch.object(service, "reset_password_code_ok", return_value=service.Result(True, "ok")) as check:
            r = self.reset()
        check.assert_called_once_with("ravi@example.com", "ABCD-EFGH")
        self.assertContains(r, "Password changed")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Brand-New-Pass-77"))
        self.assertFalse(self.owner.check_password("Old-Pass-12345"))
        self.assertTrue(self.client.login(username="ravi@example.com", password="Brand-New-Pass-77"))

    def test_wrong_or_expired_code_changes_nothing(self):
        with mock.patch.object(service, "reset_password_code_ok", return_value=service.Result(False, "That reset code is not valid or has expired.")):
            r = self.reset()
        self.assertContains(r, "not valid or has expired")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Old-Pass-12345"))

    def test_local_mistakes_never_use_up_the_code(self):
        with mock.patch.object(service, "reset_password_code_ok") as check:
            self.assertContains(self.reset(email="nobody@example.com"), "No user with this email")
            self.assertContains(self.reset(confirm="Different-Pass-1"), "do not match")
            self.assertContains(self.reset(password="short", confirm="short"), "too short")
            self.assertContains(self.reset(password="password", confirm="password"), "too common")
            self.assertContains(self.reset(code=""), "enter your email and the reset code")
        check.assert_not_called()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Old-Pass-12345"))

    def test_email_is_matched_ignoring_case(self):
        with mock.patch.object(service, "reset_password_code_ok", return_value=service.Result(True, "ok")):
            self.assertContains(self.reset(email="Ravi@Example.COM"), "Password changed")

    def test_offline_gives_the_reason(self):
        with mock.patch.object(service, "reset_password_code_ok", return_value=service.Result(False, "Could not reach the license server.")):
            self.assertContains(self.reset(), "Could not reach")

    @override_settings(LICENSE_ENFORCED=False)
    def test_development_copy_has_no_reset_code(self):
        r = self.client.get("/app/forgot/")
        self.assertContains(r, "development copy")
        self.assertNotContains(r, 'name="code"')
        with mock.patch.object(service, "reset_password_code_ok") as check:
            self.reset()
        check.assert_not_called()
        self.assertContains(self.client.get("/app/login/"), "Ask the person who installed")


# ------------------------------------------------------------------ license gate on /app/
@override_settings(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY="x", LICENSE_SERVER_URL="https://license.example.test")
class LicenseGateTests(UiTestCase):
    def setUp(self):
        super().setUp()
        service.invalidate()
        service._state["next_bg_check"] = float("inf")

    def test_unlicensed_app_pages_redirect_to_the_license_page(self):
        for url in ("/app/", "/app/scan/", "/app/search/", "/app/files/", "/app/history/"):
            self.assertRedirects(self.client.get(url), "/app/license/", fetch_redirect_response=False, msg_prefix=url)

    def test_license_page_login_and_static_stay_open(self):
        self.assertEqual(self.client.get("/app/license/").status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get("/app/login/").status_code, 200)

    def test_htmx_calls_get_a_redirect_header(self):
        r = self.client.post("/app/search/run/", {"numbers": "9856325417"}, **HX)
        self.assertEqual((r.status_code, r["HX-Redirect"]), (204, "/app/license/"))
        r = self.client.get("/app/scan/status/?task=1", **HX)
        self.assertEqual(r["HX-Redirect"], "/app/license/")

    def test_a_valid_license_opens_the_pages(self):
        with mock.patch.object(service, "current_status", return_value=protocol.LicenseStatus("active", True, "ok", license_type="monthly")), \
                mock.patch.object(service, "maybe_background_check"):
            self.assertEqual(self.client.get("/app/").status_code, 200)
            self.assertContains(self.client.get("/app/"), "Monthly plan")

    def test_chip_shows_trouble(self):
        with mock.patch("webui.context.service.current_status",
                        return_value=protocol.LicenseStatus("expired", False, "Your license expired.")):
            r = self.client.get("/app/license/")
        self.assertContains(r, "License problem")


# ------------------------------------------------------------------ chhoti cheezein
class TagTests(SimpleTestCase):
    def test_filesize(self):
        self.assertEqual(webui_tags.filesize(512), "512 B")
        self.assertEqual(webui_tags.filesize(2048), "2.0 KB")
        self.assertEqual(webui_tags.filesize(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(webui_tags.filesize("x"), "")

    def test_comma_and_clock(self):
        self.assertEqual(webui_tags.comma(1234567), "1,234,567")
        self.assertEqual(webui_tags.comma("x"), "x")
        self.assertEqual(webui_tags.clock(65), "1:05")

    def test_timeago(self):
        from datetime import timedelta
        from django.utils import timezone
        self.assertEqual(webui_tags.timeago(None), "never")
        self.assertEqual(webui_tags.timeago(timezone.now()), "just now")
        self.assertEqual(webui_tags.timeago(timezone.now() - timedelta(minutes=5)), "5 min ago")
        self.assertEqual(webui_tags.timeago(timezone.now() - timedelta(days=3)), "3 days ago")

    def test_mtime_and_folder(self):
        self.assertEqual(webui_tags.mtime(0), "")
        self.assertEqual(webui_tags.folder_of("/a/b/c.xlsx"), "/a/b")
