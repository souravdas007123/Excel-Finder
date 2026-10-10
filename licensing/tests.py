"""License (customer wali app) ke tests. Asli server ki jagah ek FakeServer use hota hai, jo wahi protocol bolta hai.

Chalane ke liye:  python manage.py test licensing
"""
import io
import json
import os
import sys
import urllib.error
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import client, fingerprint, protocol, service
from .models import LicenseState

PRIVATE, PUBLIC = protocol.generate_keypair()
OTHER_PRIVATE, OTHER_PUBLIC = protocol.generate_keypair()
PC = "a" * 64
GOOD_KEY = "EXFN-ABCDE-FGHJK-LMNPQ-RSTUV"


class FakeServer:
    """License server ki nakal: register / activate / check / deactivate / reset ka jawab, aur kya-kya bheja gaya uska record."""

    def __init__(self):
        self.calls = []
        self.unreachable = False
        self.license_type = "yearly"
        self.expires = lambda now: now + timedelta(days=365)
        self.check_by_days = 14
        self.error = None            # (http, code, message) ya None
        self.private = PRIVATE
        self.clock = protocol.utcnow            # test me badal sakte hain (time travel)

    def token(self, machine, now=None):
        now = now or self.clock()
        payload = protocol.make_payload(
            license_id="lic-1", license_type=self.license_type, customer="Ravi Traders", machine=machine,
            issued=now, expires=None if self.license_type == "lifetime" else self.expires(now),
            check_by=now + timedelta(days=self.check_by_days))
        return protocol.sign_token(payload, self.private)

    def __call__(self, action, payload, timeout=12):
        self.calls.append((action, payload))
        if self.unreachable:
            raise client.ServerUnreachable("Could not reach the license server. Please check your internet connection.")
        if self.error:
            http, code, message = self.error
            return http, {"ok": False, "error": code, "message": message}
        if action == "activate" and protocol.normalize_key(payload.get("key")) != GOOD_KEY:
            return 404, {"ok": False, "error": "invalid_key", "message": "This license key is not valid."}
        if action == "deactivate":
            return 200, {"ok": True, "deactivated": True}
        if action == "register":
            return 200, {"ok": True, "message": "Request received. The seller will email you a license key."}
        if action == "reset":
            return 200, {"ok": True, "message": "Reset code accepted."}
        now = self.clock()
        expires = None if self.license_type == "lifetime" else self.expires(now)
        return 200, {"ok": True, "token": self.token(payload["machine_id"], now), "server_time": protocol.iso(now),
                     "license": {"type": self.license_type, "customer": "Ravi Traders", "expires": protocol.iso(expires)}}


LICENSE_SETTINGS = dict(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY=PUBLIC, LICENSE_SERVER_URL="https://license.example.test",
                        LICENSE_CHECK_INTERVAL_HOURS=24, LICENSE_WARN_DAYS=14)


@override_settings(**LICENSE_SETTINGS)
class LicenseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        cls.staff = User.objects.create_user("staff", "s@example.com", "pw", is_staff=True)

    def setUp(self):
        self.server = FakeServer()
        for patcher in (mock.patch.object(client, "call", self.server),
                        mock.patch.object(service, "machine_id", lambda: PC),
                        mock.patch.object(service, "machine_name", lambda: "OFFICE-PC")):
            patcher.start()
            self.addCleanup(patcher.stop)
        service.invalidate()
        service._state["next_bg_check"] = float("inf")    # test ke dauran asli background thread nahi (DB lock se bachne ke liye)
        self.client.force_login(self.admin)

    def activate_ok(self):
        result = service.activate(GOOD_KEY)
        self.assertTrue(result.ok, result.message)
        service.invalidate()
        return result

    def store_token(self, **kw):
        """Seedha token rakh do (time travel ke liye)."""
        now = kw.pop("now", protocol.utcnow())
        payload = protocol.make_payload(
            license_id="x", license_type=kw.get("type", "yearly"), customer="Ravi Traders", machine=kw.get("machine", PC),
            issued=kw.get("issued", now), expires=kw.get("expires", now + timedelta(days=100)),
            check_by=kw.get("check_by", now + timedelta(days=14)))
        state = LicenseState.get()
        state.token, state.license_key = protocol.sign_token(payload, kw.get("private", PRIVATE)), kw.get("key", GOOD_KEY)
        state.save()
        service.invalidate()

    def hx(self, name, data=None, **kw):
        """Naye UI ka htmx POST (jawab me toast ka text hota hai)."""
        return self.client.post(reverse(name), data or {}, HTTP_HX_REQUEST="true", **kw)


# ------------------------------------------------------------------ pehle: kab block hota hai
class BlockingTests(LicenseTestCase):
    PAGES = ("webui:dashboard", "webui:scan", "webui:search", "webui:files", "webui:history")

    def test_unlicensed_app_redirects_pages_to_the_license_page(self):
        license_page = reverse("webui:license")
        for name in self.PAGES:
            r = self.client.get(reverse(name))
            self.assertRedirects(r, license_page, fetch_redirect_response=False, msg_prefix=name)
        r = self.client.get(license_page)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Key needed")
        self.assertContains(r, "Activate your license")
        self.assertNotContains(r, "Create an account")

    def test_actions_are_refused_too(self):
        for name in ("webui:search_run", "webui:scan_start", "webui:scan_clear"):          # htmx: poora page badalkar License par
            r = self.hx(name, {"numbers": "9856325417"})
            self.assertEqual((r.status_code, r["HX-Redirect"]), (204, reverse("webui:license")), name)
        r = self.client.post(reverse("webui:search_export"), {"numbers": "9856325417"})        # normal form: download nahi hota
        self.assertRedirects(r, reverse("webui:license"), fetch_redirect_response=False)

    def test_login_and_logout_stay_open(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("webui:login")).status_code, 200)

    def test_there_is_no_django_admin_in_the_customer_app(self):
        self.activate_ok()
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_active_license_opens_everything(self):
        self.activate_ok()
        for name in self.PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.hx("webui:search_run", {"numbers": "9856325417"}).status_code, 200)

    def test_expired_license_is_blocked_and_says_when_it_expired(self):
        now = protocol.utcnow()
        self.store_token(issued=now - timedelta(days=400), expires=now - timedelta(days=2), check_by=now + timedelta(days=5))
        r = self.client.get(reverse("webui:search"))
        self.assertEqual(r.status_code, 302)
        page = self.client.get(reverse("webui:license"))
        self.assertContains(page, "Expired")
        self.assertContains(page, "expired on")
        self.assertEqual(self.hx("webui:search_run", {"numbers": "9856325417"}).status_code, 204)

    def test_a_token_from_another_pc_is_rejected(self):
        self.store_token(machine="b" * 64)
        self.assertEqual(service.current_status().code, "wrong_machine")

    def test_a_forged_token_signed_with_another_key_is_rejected(self):
        self.store_token(private=OTHER_PRIVATE)
        self.assertEqual(service.current_status().code, "invalid")
        self.assertEqual(self.client.get(reverse("webui:dashboard")).status_code, 302)

    def test_editing_the_token_in_the_database_is_detected(self):
        self.activate_ok()
        state = LicenseState.get()
        body, signature = state.token.split(".")
        forged = protocol._b64e(json.dumps({**json.loads(protocol._b64d(body)), "type": "lifetime", "expires": None}).encode())
        state.token = f"{forged}.{signature}"
        state.save()
        service.invalidate()
        self.assertEqual(service.current_status().code, "invalid")

    def test_too_long_offline_blocks_until_checked_online(self):
        now = protocol.utcnow()
        self.store_token(issued=now - timedelta(days=30), check_by=now - timedelta(days=16))
        status = service.current_status()
        self.assertEqual(status.code, "offline_overdue")
        self.server.unreachable = True
        result = service.check_now()
        self.assertFalse(result.ok)
        service.invalidate()
        self.assertFalse(service.current_status().ok)                 # internet nahi: ab bhi blocked
        self.server.unreachable = False
        self.assertTrue(service.check_now().ok)
        self.assertTrue(service.current_status().ok)                  # check hote hi chalu

    def test_clock_set_back_is_detected_and_a_server_check_fixes_it(self):
        self.activate_ok()
        LicenseState.objects.update(max_seen_time=timezone.now() + timedelta(days=10))
        service.invalidate()
        self.assertEqual(service.current_status().code, "clock")
        self.assertTrue(service.check_now().ok)
        self.assertTrue(service.current_status().ok)

    def test_lifetime_license_has_no_expiry(self):
        self.server.license_type = "lifetime"
        self.activate_ok()
        status = service.current_status()
        self.assertTrue(status.ok)
        self.assertIsNone(status.expires)
        page = self.client.get(reverse("webui:license"))
        self.assertContains(page, "Never (lifetime)")


class ExpiryWarningTests(LicenseTestCase):
    def test_warns_when_expiry_is_near_but_still_works(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=5))
        status = service.current_status()
        self.assertTrue(status.ok)
        self.assertIn("expires on", status.warn)
        self.assertEqual(status.days_left, 5)

    def test_warning_is_shown_on_every_page_until_renewed(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=3))
        for name in ("webui:dashboard", "webui:scan", "webui:search"):
            self.assertContains(self.client.get(reverse(name)), "expires on", msg_prefix=name)
        self.store_token(expires=protocol.utcnow() + timedelta(days=200))
        self.assertNotContains(self.client.get(reverse("webui:dashboard")), "Please renew")

    def test_no_warning_when_far_from_expiry(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=200))
        self.assertEqual(service.current_status().warn, "")


# ------------------------------------------------------------------ server se baat
class ActivationFlowTests(LicenseTestCase):
    def test_activating_with_a_key_typed_loosely(self):
        result = service.activate("  exfn abcde fghjk lmnpq rstuv ")
        self.assertTrue(result.ok, result.message)
        self.assertIn("License activated", result.message)
        self.assertEqual(LicenseState.get().license_key, GOOD_KEY)
        status = service.current_status(force=True)
        self.assertTrue(status.ok)
        self.assertEqual(status.customer, "Ravi Traders")

    def test_sent_data_contains_only_the_agreed_fields(self):
        self.server.calls.clear()
        service.activate(GOOD_KEY)
        action, payload = self.server.calls[0]
        self.assertEqual(action, "activate")
        self.assertEqual(set(payload), {"key", "machine_id", "machine_name", "usage"})
        self.assertEqual(set(payload["usage"]), {"searches_total", "scans_total", "files_indexed"})

    def test_wrong_key_shows_the_server_message_and_stays_blocked(self):
        result = service.activate("EXFN-AAAAA-AAAAA-AAAAA-AAAAA")
        self.assertIn("not valid", result.message)
        self.assertFalse(service.current_status(force=True).ok)

    def test_garbage_key_is_rejected_without_calling_the_server(self):
        self.server.calls.clear()
        result = service.activate("hello")
        self.assertFalse(result.ok)
        self.assertEqual(self.server.calls, [])

    def test_server_down_during_activation(self):
        self.server.unreachable = True
        result = service.activate(GOOD_KEY)
        self.assertEqual((result.ok, "internet" in result.message), (False, True))
        self.assertEqual(LicenseState.get().token, "")

    def test_reply_signed_by_another_server_is_not_accepted(self):
        self.server.private = OTHER_PRIVATE                           # galat / nakli server
        result = service.activate(GOOD_KEY)
        self.assertFalse(result.ok)
        self.assertIn("could not be verified", result.message)
        self.assertEqual(LicenseState.get().token, "")

    def test_machine_limit_message_is_shown(self):
        self.server.error = (409, "machine_limit", "This license is already used on 1 PC (limit 1).")
        result = service.activate(GOOD_KEY)
        self.assertEqual((result.ok, "already used" in result.message), (False, True))

    def test_what_is_sent_is_explained_on_the_page(self):
        r = self.client.get(reverse("webui:license"))
        self.assertContains(r, "never sends your Excel files")


class CheckTests(LicenseTestCase):
    def test_check_refreshes_the_token_and_sends_usage(self):
        self.activate_ok()
        LicenseState.objects.update(searches_total=7, scans_total=2)
        old = LicenseState.get().token
        self.server.calls.clear()
        self.server.clock = lambda: protocol.utcnow() + timedelta(minutes=5)     # server ka naya token naye time ka hoga
        self.assertTrue(service.check_now().ok)
        action, payload = self.server.calls[0]
        self.assertEqual((action, payload["usage"]), ("check", {"searches_total": 7, "scans_total": 2, "files_indexed": 0}))
        self.assertNotEqual(LicenseState.get().token, old)
        self.assertIsNotNone(LicenseState.get().last_check_at)

    def test_revoked_license_is_blocked_at_the_next_check(self):
        self.activate_ok()
        self.server.error = (403, "revoked", "Payment failed. Contact support.")
        self.assertFalse(service.check_now().ok)
        service.invalidate()
        status = service.current_status()
        self.assertEqual((status.code, status.ok), ("revoked", False))
        self.assertEqual(self.client.get(reverse("webui:dashboard")).status_code, 302)
        page = self.client.get(reverse("webui:license"))
        self.assertContains(page, "Cancelled")
        self.assertContains(page, "Payment failed")

    def test_renewal_unblocks_after_the_next_check(self):
        self.activate_ok()
        self.server.error = (403, "expired", "Your license expired on 01 Jan 2027. Please renew.")
        service.check_now()
        self.assertEqual(service.current_status(force=True).code, "expired")
        self.server.error = None                                      # seller ne renew kar diya
        self.assertTrue(service.check_now().ok)
        self.assertTrue(service.current_status(force=True).ok)

    def test_deactivated_pc_must_activate_again(self):
        self.activate_ok()
        self.server.error = (409, "not_activated", "This PC is not activated for this license.")
        service.check_now()
        self.assertEqual(service.current_status(force=True).code, "not_activated")

    def test_rotated_key_asks_for_the_new_key(self):
        self.activate_ok()
        self.server.error = (404, "invalid_key", "invalid")
        service.check_now()
        status = service.current_status(force=True)
        self.assertEqual(status.code, "revoked")
        self.assertIn("no longer valid", status.message)

    def test_server_down_does_not_block_a_valid_license(self):
        self.activate_ok()
        self.server.unreachable = True
        result = service.check_now()
        self.assertFalse(result.ok)
        service.invalidate()
        self.assertTrue(service.current_status().ok)                  # grace period me chalta rahega
        state = LicenseState.get()
        self.assertFalse(state.last_check_ok)
        self.assertIn("internet", state.last_error)

    def test_check_without_any_license(self):
        self.assertFalse(service.check_now().ok)
        self.assertEqual(self.server.calls, [])


class DeactivateTests(LicenseTestCase):
    def test_deactivate_releases_the_seat_and_clears_the_pc(self):
        self.activate_ok()
        self.server.calls.clear()
        result = service.deactivate()
        self.assertTrue(result.ok)
        self.assertEqual(self.server.calls[0][0], "deactivate")
        state = LicenseState.get()
        self.assertEqual((state.token, state.license_key), ("", ""))
        self.assertEqual(service.current_status(force=True).code, "unlicensed")

    def test_deactivate_needs_the_internet_and_keeps_the_license_otherwise(self):
        self.activate_ok()
        self.server.unreachable = True
        result = service.deactivate()
        self.assertFalse(result.ok)
        self.assertTrue(LicenseState.get().token)
        self.assertTrue(service.current_status(force=True).ok)


class UsageTests(LicenseTestCase):
    def test_searches_and_scans_are_counted(self):
        self.activate_ok()
        for _ in range(3):
            self.hx("webui:search_run", {"numbers": "9856325417"})
        from fileindex.scanner import scan_lock
        self.addCleanup(lambda: scan_lock.locked() and scan_lock.release())      # run_scan nakli hai, lock wo chhodta nahi
        with mock.patch("fileindex.services.run_scan"):
            self.hx("webui:scan_start", {"location": "__custom__", "custom_path": os.path.dirname(__file__)})
        state = LicenseState.get()
        self.assertEqual(state.searches_total, 3)
        self.assertEqual(state.scans_total, 1)

    def test_files_indexed_is_sent_as_a_plain_count(self):
        from fileindex.models import FileIndex
        for i in range(3):
            FileIndex.objects.create(file_name=f"f{i}.xlsx", file_path=f"/data/f{i}.xlsx")
        self.activate_ok()
        self.server.calls.clear()
        service.check_now()
        self.assertEqual(self.server.calls[0][1]["usage"]["files_indexed"], 3)
        self.assertNotIn("f0.xlsx", str(self.server.calls))               # file ka naam / path kabhi nahi jata

    def test_counting_never_breaks_a_search(self):
        self.activate_ok()
        with mock.patch.object(LicenseState.objects, "filter", side_effect=RuntimeError("db down")), \
                self.assertLogs("licensing.service", level="ERROR"):
            service.record_usage("search")           # exception bahar nahi aani chahiye


class RequestTests(LicenseTestCase):
    """Customer naam + email deta hai, seller key email karta hai. App password server ko nahi bhejta."""

    def test_register_sends_only_name_email_and_the_pc(self):
        result = service.register(" Ravi Kumar ", "Ravi@Example.com ")
        self.assertTrue(result.ok, result.message)
        action, payload = self.server.calls[-1]
        self.assertEqual(action, "register")
        self.assertEqual(payload, {"name": "Ravi Kumar", "email": "ravi@example.com", "machine_id": PC, "machine_name": "OFFICE-PC"})

    def test_register_remembers_who_you_are_and_the_app_stays_closed_until_a_key(self):
        service.register("Ravi Kumar", "ravi@example.com")
        state = LicenseState.get()
        self.assertEqual((state.account_name, state.account_email), ("Ravi Kumar", "ravi@example.com"))
        status = service.current_status(force=True)
        self.assertEqual((status.code, status.ok), ("unlicensed", False))

    def test_register_needs_the_internet_and_remembers_nothing_on_failure(self):
        self.server.unreachable = True
        result = service.register("Ravi", "ravi@example.com")
        self.assertEqual((result.ok, "internet" in result.message), (False, True))
        self.assertEqual(LicenseState.get().account_email, "")

    def test_server_refusal_is_shown(self):
        self.server.error = (400, "bad_email", "Please enter a valid email address.")
        result = service.register("Ravi", "nope")
        self.assertEqual((result.ok, result.message), (False, "Please enter a valid email address."))
        self.assertEqual(LicenseState.get().account_email, "")

    def test_key_from_the_seller_then_unlocks_the_app(self):
        service.register("Ravi Kumar", "ravi@example.com")
        self.assertTrue(service.activate(GOOD_KEY).ok)
        service.invalidate()
        self.assertTrue(service.current_status().ok)
        self.assertEqual(self.client.get(reverse("webui:dashboard")).status_code, 200)

    def test_background_checks_need_a_key(self):
        service._state["next_bg_check"] = 0.0
        service.register("Ravi", "ravi@example.com")
        with mock.patch.object(service.threading, "Thread") as thread:
            service.maybe_background_check()
        thread.assert_not_called()


class ResetCodeTests(LicenseTestCase):
    def test_code_is_checked_on_the_server_and_the_reply_is_passed_on(self):
        result = service.reset_password_code_ok(" Ravi@Example.com", "ABCD-EFGH")
        self.assertTrue(result.ok)
        self.assertEqual(self.server.calls[-1], ("reset", {"email": "ravi@example.com", "code": "ABCD-EFGH"}))

    def test_wrong_code(self):
        self.server.error = (403, "bad_code", "That reset code is not valid or has expired.")
        result = service.reset_password_code_ok("ravi@example.com", "AAAA-AAAA")
        self.assertEqual((result.ok, result.message), (False, "That reset code is not valid or has expired."))

    def test_needs_the_internet(self):
        self.server.unreachable = True
        result = service.reset_password_code_ok("ravi@example.com", "ABCD-EFGH")
        self.assertEqual((result.ok, "internet" in result.message), (False, True))


class BackgroundCheckTests(LicenseTestCase):
    def setUp(self):
        super().setUp()
        service._state["next_bg_check"] = 0.0

    def run_maybe(self):
        started = []

        class FakeThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                started.append(self.target)

        with mock.patch.object(service.threading, "Thread", FakeThread):
            service.maybe_background_check()
        return started

    def test_check_starts_when_the_last_one_is_older_than_a_day(self):
        self.activate_ok()
        LicenseState.objects.update(last_check_at=timezone.now() - timedelta(hours=30))
        self.assertEqual(len(self.run_maybe()), 1)

    def test_no_check_when_recent(self):
        self.activate_ok()
        self.assertEqual(self.run_maybe(), [])

    def test_no_check_without_a_license(self):
        self.assertEqual(self.run_maybe(), [])

    def test_retries_are_rate_limited(self):
        self.activate_ok()
        LicenseState.objects.update(last_check_at=timezone.now() - timedelta(hours=30))
        self.assertEqual(len(self.run_maybe()), 1)
        self.assertEqual(self.run_maybe(), [])       # 5 minute ke andar dobara nahi

    def test_worker_runs_a_check_and_survives_errors(self):
        with mock.patch.object(service, "check_now", side_effect=RuntimeError("boom")), \
                mock.patch.object(service, "close_old_connections"), mock.patch.object(service.connection, "close"), \
                self.assertLogs("licensing.service", level="ERROR"):
            service._background_worker()             # panic nahi

    @override_settings(LICENSE_ENFORCED=False)
    def test_nothing_happens_when_not_enforced(self):
        self.assertEqual(self.run_maybe(), [])


@override_settings(LICENSE_ENFORCED=False)
class NotEnforcedTests(TestCase):
    """Developer copy: license kuch nahi rokta (aur pehle ke saare features waise hi chalte hain)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")

    def setUp(self):
        service.invalidate()
        self.client.force_login(self.admin)

    def test_everything_is_open(self):
        self.assertTrue(service.current_status().ok)
        self.assertEqual(self.client.get(reverse("webui:search")).status_code, 200)
        self.assertEqual(self.client.post(reverse("webui:search_run"), {"numbers": "9856325417"}, HTTP_HX_REQUEST="true").status_code, 200)

    def test_license_page_says_checks_are_off(self):
        r = self.client.get(reverse("webui:license"))
        self.assertContains(r, "Not enforced")

    def test_sidebar_has_the_license_link(self):
        self.assertContains(self.client.get(reverse("webui:dashboard")), reverse("webui:license"))


@override_settings(LICENSE_ENFORCED=True, LICENSE_PUBLIC_KEY="")
class MisconfiguredBuildTests(TestCase):
    def test_enforced_build_without_a_public_key_blocks_instead_of_opening(self):
        service.invalidate()
        status = service.current_status()
        self.assertEqual((status.ok, status.code), (False, "invalid"))


# ------------------------------------------------------------------ chhote hisse
class ClientHttpTests(SimpleTestCase):
    @override_settings(LICENSE_SERVER_URL="https://license.example.test/")
    def test_success_and_error_replies_are_parsed(self):
        class Reply(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Reply(b'{"ok": true, "token": "t"}')) as opened:
            status, data = client.call("check", {"key": "k"})
        self.assertEqual((status, data["token"]), (200, "t"))
        request = opened.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/api/v1/check"))
        self.assertIn("app_version", json.loads(request.data))

        error = urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b'{"ok": false, "error": "revoked", "message": "m"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            self.assertEqual(client.call("check", {})[1]["error"], "revoked")
        html = urllib.error.HTTPError("u", 502, "x", {}, io.BytesIO(b"<html>bad gateway</html>"))
        with mock.patch("urllib.request.urlopen", side_effect=html):
            status, data = client.call("check", {})
        self.assertEqual((status, data["error"]), (502, "http_error"))

    @override_settings(LICENSE_SERVER_URL="https://license.example.test")
    def test_network_problems_become_server_unreachable(self):
        for exc in (urllib.error.URLError("dns"), TimeoutError(), ConnectionResetError()):
            with mock.patch("urllib.request.urlopen", side_effect=exc):
                with self.assertRaises(client.ServerUnreachable):
                    client.call("check", {})

    def test_server_address_rules(self):
        with override_settings(LICENSE_SERVER_URL=""):
            with self.assertRaises(client.ServerUnreachable):
                client._base_url()
        with override_settings(LICENSE_SERVER_URL="http://license.example.test"):
            with self.assertRaises(client.ServerUnreachable) as ctx:
                client._base_url()
            self.assertIn("https", str(ctx.exception))
        for ok in ("https://license.example.test", "http://127.0.0.1:8800", "http://localhost:8800/"):
            with override_settings(LICENSE_SERVER_URL=ok):
                self.assertTrue(client._base_url().startswith("http"))


class FingerprintTests(SimpleTestCase):
    def setUp(self):
        fingerprint.machine_id.cache_clear()
        self.addCleanup(fingerprint.machine_id.cache_clear)

    def test_id_is_a_stable_64_char_hash(self):
        a = fingerprint.machine_id()
        fingerprint.machine_id.cache_clear()
        self.assertEqual(a, fingerprint.machine_id())
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_override_works_in_source_copies_but_not_in_the_installed_exe(self):
        with mock.patch.dict(os.environ, {"EXCEL_FINDER_MACHINE_ID": "test-pc-1"}):
            fingerprint.machine_id.cache_clear()
            overridden = fingerprint.machine_id()
            with mock.patch.object(sys, "frozen", True, create=True):
                fingerprint.machine_id.cache_clear()
                self.assertNotEqual(fingerprint.machine_id(), overridden)
        fingerprint.machine_id.cache_clear()

    def test_name(self):
        self.assertTrue(fingerprint.machine_name())
