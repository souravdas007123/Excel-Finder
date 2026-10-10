"""'Naya version available hai' notice ke tests.  Chalane ke liye:  python manage.py test search.test_updates"""
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import middleware as first_run
from . import update_check

GOOD = {"version": "1.2.0", "released": "2026-11-01", "download_url": "https://example.com/download",
        "sha256": "a" * 64, "notes": ["Faster scans", "Bug fixes"]}


class ManifestServer:
    """Chhota local web server jo latest.json ki jagah jawab deta hai (aur galat jawab bhi de sakta hai)."""

    def __init__(self):
        outer = self
        self.status, self.body, self.hits = 200, json.dumps(GOOD).encode(), 0

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/latest.json"

    def set(self, data):
        self.body = json.dumps(data).encode() if not isinstance(data, bytes) else data

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class UpdateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        cls.outsider = User.objects.create_user("outsider", "o@example.com", "pw", is_staff=False)

    def setUp(self):
        first_run.reset_first_run_cache()
        self.server = ManifestServer()
        self.addCleanup(self.server.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(UPDATE_CHECK_URL=self.server.url, UPDATE_CACHE_FILE=Path(self.tmp.name) / "update_info.json")
        override.enable()
        self.addCleanup(override.disable)
        patcher = mock.patch.object(update_check, "VERSION", "1.0.0")
        patcher.start()
        self.addCleanup(patcher.stop)
        update_check._state["next_attempt"] = float("inf")     # test ke dauran asli background thread nahi (file race se bachne ke liye)
        self.client.force_login(self.admin)

    def page(self, name="webui:dashboard"):
        return self.client.get(reverse(name))


class VersionTests(SimpleTestCase):
    def test_parse_and_compare(self):
        self.assertEqual(update_check.parse_version("1.2"), (1, 2, 0, 0))
        self.assertEqual(update_check.parse_version("1.2.3.4"), (1, 2, 3, 4))
        for bad in ("", "1", "a.b", "1.2.x", "1.2.3.4.5", None, 5, "v1.2"):
            self.assertIsNone(update_check.parse_version(bad), bad)
        self.assertTrue(update_check.is_newer("1.10.0", "1.9.9"))        # 10 > 9: text ki tarah nahi
        self.assertTrue(update_check.is_newer("2.0", "1.99.99"))
        self.assertFalse(update_check.is_newer("1.0.0", "1.0"))
        self.assertFalse(update_check.is_newer("0.9", "1.0"))
        self.assertFalse(update_check.is_newer("junk", "1.0"))


class ManifestValidationTests(SimpleTestCase):
    def test_good_manifest_is_cleaned(self):
        m = update_check.validate_manifest({**GOOD, "notes": "one line", "extra": "ignored", "min_version": "1.1"})
        self.assertEqual((m["version"], m["notes"], m["min_version"]), ("1.2.0", ["one line"], "1.1"))
        self.assertNotIn("extra", m)

    def test_bad_manifests_are_rejected(self):
        for bad in (None, [], "text", {}, {"version": "x"}, {**GOOD, "version": "1"},
                    {**GOOD, "download_url": "http://example.com/x"},          # http nahi
                    {**GOOD, "download_url": "javascript:alert(1)"},
                    {**GOOD, "download_url": "file:///c:/x.exe"},
                    {**GOOD, "download_url": None}, {**GOOD, "notes": {"a": 1}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                update_check.validate_manifest(bad)

    def test_localhost_http_is_allowed_for_testing_only(self):
        self.assertEqual(update_check.validate_manifest({**GOOD, "download_url": "http://127.0.0.1:8000/x"})["download_url"],
                         "http://127.0.0.1:8000/x")

    def test_notes_and_hash_are_limited(self):
        m = update_check.validate_manifest({**GOOD, "sha256": "zzz", "notes": ["x" * 500] + [str(i) for i in range(30)] + ["", "  "]})
        self.assertEqual(m["sha256"], "")
        self.assertEqual(len(m["notes"]), update_check.MAX_NOTES)
        self.assertEqual(len(m["notes"][0]), update_check.MAX_NOTE_LEN)


class CheckTests(UpdateTestCase):
    def test_finds_a_newer_version_and_saves_it(self):
        result = update_check.check_now()
        self.assertEqual((result.ok, "1.2.0 is available" in result.message), (True, True))
        self.assertEqual(update_check.current_notice()["version"], "1.2.0")

    def test_up_to_date_and_older_server_show_no_notice(self):
        for version in ("1.0.0", "0.9.0"):
            self.server.set({**GOOD, "version": version})
            result = update_check.check_now()
            self.assertIn("latest version", result.message)
            self.assertIsNone(update_check.current_notice())

    def test_server_problems_never_raise_and_keep_the_last_known_update(self):
        update_check.check_now()
        for status, body in ((404, b"nope"), (200, b"not json"), (200, b'{"version": "bad"}'), (200, b"x" * 70000)):
            self.server.status, self.server.body = status, body
            result = update_check.check_now()
            self.assertFalse(result.ok, (status, body[:10]))
        self.assertEqual(update_check.current_notice()["version"], "1.2.0")      # purani jaankari bachi

    def test_unreachable_server(self):
        self.server.close()
        result = update_check.check_now()
        self.assertEqual((result.ok, "internet" in result.message), (False, True))

    def test_https_is_required_for_real_addresses(self):
        with override_settings(UPDATE_CHECK_URL="http://updates.example.com/latest.json"):
            result = update_check.check_now()
        self.assertFalse(result.ok)

    def test_nothing_happens_when_no_address_is_set(self):
        with override_settings(UPDATE_CHECK_URL=""):
            self.assertFalse(update_check.enabled())
            self.assertIsNone(update_check.current_notice())
            update_check.maybe_background_check()
        self.assertEqual(self.server.hits, 0)


class NoticeTests(UpdateTestCase):
    def test_banner_shows_notes_and_a_download_link_on_every_page(self):
        update_check.check_now()
        for name in ("webui:dashboard", "webui:scan", "webui:search", "webui:license"):
            r = self.page(name)
            self.assertContains(r, "Excel Finder 1.2.0 is available", msg_prefix=name)
            self.assertContains(r, "Faster scans")
            self.assertContains(r, 'href="https://example.com/download"')
            self.assertNotContains(r, "no longer supported")

    def test_no_banner_without_an_update(self):
        self.assertNotContains(self.page(), "is available (you have")

    def test_not_now_hides_it_until_a_newer_version(self):
        update_check.check_now()
        r = self.client.post(reverse("webui:update_dismiss"), {"version": "1.2.0"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "is available")                     # naya banner khali
        self.assertNotContains(self.page(), "is available")
        self.server.set({**GOOD, "version": "1.3.0"})
        update_check.check_now()
        self.assertContains(self.page(), "Excel Finder 1.3.0 is available")

    def test_required_update_is_red_and_cannot_be_dismissed(self):
        self.server.set({**GOOD, "min_version": "1.1.0"})
        update_check.check_now()
        update_check.dismiss("1.2.0")
        r = self.page()
        self.assertContains(r, "no longer supported")
        self.assertContains(r, 'class="alert bad"')
        self.assertNotContains(r, "Not now")

    def test_notes_cannot_inject_html(self):
        self.server.set({**GOOD, "notes": ["<script>alert(1)</script>", "<b>bold</b>"]})
        update_check.check_now()
        html = self.page().content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_banner_is_for_signed_in_staff_only(self):
        update_check.check_now()
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("webui:login")), "is available (you have")
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(reverse("webui:login")), "is available (you have")

    def test_actions_need_post_and_login(self):
        self.assertEqual(self.client.get(reverse("webui:update_check")).status_code, 405)
        self.assertEqual(self.client.get(reverse("webui:update_dismiss")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("webui:update_check")).status_code, 302)

    def test_check_button(self):
        r = self.client.post(reverse("webui:update_check"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "Version 1.2.0 is available")
        self.assertContains(r, 'id="update-banner"')               # banner bhi saath badalta hai
        self.server.set({**GOOD, "version": "1.0.0"})
        r = self.client.post(reverse("webui:update_check"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "You have the latest version")

    def test_check_button_when_not_configured(self):
        with override_settings(UPDATE_CHECK_URL=""):
            r = self.client.post(reverse("webui:update_check"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "not set up")

    def test_update_pages_stay_open_without_a_license(self):
        with override_settings(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY="x"):
            r = self.client.post(reverse("webui:update_check"), HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Version 1.2.0 is available")


class BackgroundTests(UpdateTestCase):
    def setUp(self):
        super().setUp()
        update_check._state["next_attempt"] = 0.0

    def run_maybe(self):
        started = []

        class FakeThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                started.append(self.target)

        with mock.patch.object(update_check.threading, "Thread", FakeThread):
            update_check.maybe_background_check()
        return started

    def test_checks_when_never_checked_then_waits_a_day(self):
        self.assertEqual(len(self.run_maybe()), 1)
        update_check._state["next_attempt"] = 0.0
        update_check.check_now()                                     # abhi check hua
        self.assertEqual(self.run_maybe(), [])
        state = json.loads(update_check._cache_path().read_text())
        state["checked_at"] = "2020-01-01T00:00:00+00:00"            # purana check
        update_check._cache_path().write_text(json.dumps(state))
        update_check._state["next_attempt"] = 0.0
        self.assertEqual(len(self.run_maybe()), 1)

    def test_failed_attempts_are_not_repeated_every_request(self):
        self.assertEqual(len(self.run_maybe()), 1)
        self.assertEqual(self.run_maybe(), [])                        # 5 minute ke andar dobara nahi

    def test_only_app_get_requests_of_staff_trigger_it(self):
        with mock.patch.object(update_check, "maybe_background_check") as trigger:
            self.client.get(reverse("webui:dashboard"))
            self.assertEqual(trigger.call_count, 1)
            self.client.post(reverse("webui:update_dismiss"), {"version": "1.2.0"})
            self.client.logout()
            self.client.get(reverse("webui:login"))
            self.assertEqual(trigger.call_count, 1)

    def test_worker_survives_errors(self):
        with mock.patch.object(update_check, "check_now", side_effect=RuntimeError("boom")), \
                self.assertLogs("search.update_check", level="ERROR"):
            update_check._worker()


class StateFileTests(UpdateTestCase):
    def test_corrupt_or_missing_file_is_harmless(self):
        path = update_check._cache_path()
        self.assertIsNone(update_check.current_notice())
        path.write_text("{broken")
        self.assertIsNone(update_check.current_notice())
        path.write_text('["a list"]')
        self.assertIsNone(update_check.current_notice())
        self.assertTrue(update_check.check_now().ok)                  # phir se theek ho jata hai

    def test_notice_ignores_tampered_state(self):
        update_check._cache_path().write_text(json.dumps({"latest": {"version": "9.9.9", "download_url": "javascript:x"}}))
        notice = update_check.current_notice()
        # file customer ki hai: link ab bhi sirf template me escape hokar jata hai, aur check_now isko dobara likh deta hai
        self.assertEqual(notice["version"], "9.9.9")
