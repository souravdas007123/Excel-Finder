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
from django.contrib.messages import get_messages
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
    """License server ki nakal: activate / check / deactivate / trial ka jawab, aur kya-kya bheja gaya uska record."""

    def __init__(self):
        self.calls = []
        self.unreachable = False
        self.license_type = "yearly"
        self.expires = lambda now: now + timedelta(days=365)
        self.check_by_days = 14
        self.error = None            # (http, code, message) ya None
        self.private = PRIVATE
        self.clock = protocol.utcnow            # test me badal sakte hain (time travel)
        self.pending = False                    # account hai par seller ne plan nahi diya

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
        if action in ("deactivate", "logout"):
            return 200, {"ok": True, "deactivated": True}
        now = self.clock()
        if action in ("register", "login", "account_check"):
            account = {"email": payload["email"].lower(), "name": payload.get("name", "Ravi")}
            if self.pending:
                return 200, {"ok": True, "pending": True, "message": "Your account is created and waiting for approval.",
                             "device_token": "device-secret-1", "account": account}
            reply = {"ok": True, "token": self.token(payload["machine_id"], now), "server_time": protocol.iso(now),
                     "license": {"type": self.license_type, "customer": "Ravi Traders",
                                 "expires": protocol.iso(None if self.license_type == "lifetime" else self.expires(now))},
                     "device_token": "device-secret-1", "account": account}
            return 200, reply
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

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ------------------------------------------------------------------ pehle: kab block hota hai
class BlockingTests(LicenseTestCase):
    def test_unlicensed_app_redirects_pages_to_the_license_page(self):
        license_page = reverse("admin:licensing_licensestate_changelist")
        for name in ("admin:index", "admin:fileindex_fileindex_changelist", "admin:fileindex_bulksearch_changelist",
                     "admin:fileindex_scantask_changelist"):
            r = self.client.get(reverse(name))
            self.assertRedirects(r, license_page, fetch_redirect_response=False, msg_prefix=name)
        r = self.client.get(license_page)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "NOT SIGNED IN")
        self.assertContains(r, "Sign in to your account")
        self.assertContains(r, "Create an account")

    def test_apis_answer_402_with_the_reason(self):
        for name in ("bulk_search", "start_scan", "export_excel", "clear_index"):
            r = self.client.post(reverse(name), {})
            self.assertEqual(r.status_code, 402, name)
            self.assertIn("No license activated", r.json()["error"])
            self.assertEqual(r.json()["license"], "unlicensed")

    def test_login_and_logout_stay_open(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)

    def test_active_license_opens_everything(self):
        self.activate_ok()
        for name in ("admin:index", "admin:fileindex_fileindex_changelist", "admin:fileindex_bulksearch_changelist"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.post(reverse("bulk_search"), {"numbers": "9856325417"}).status_code, 200)

    def test_expired_license_is_blocked_and_says_when_it_expired(self):
        now = protocol.utcnow()
        self.store_token(issued=now - timedelta(days=400), expires=now - timedelta(days=2), check_by=now + timedelta(days=5))
        r = self.client.get(reverse("admin:fileindex_bulksearch_changelist"))
        self.assertEqual(r.status_code, 302)
        page = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertContains(page, "EXPIRED")
        self.assertContains(page, "expired on")
        self.assertEqual(self.client.post(reverse("bulk_search"), {"numbers": "9856325417"}).status_code, 402)

    def test_a_token_from_another_pc_is_rejected(self):
        self.store_token(machine="b" * 64)
        self.assertEqual(service.current_status().code, "wrong_machine")

    def test_a_forged_token_signed_with_another_key_is_rejected(self):
        self.store_token(private=OTHER_PRIVATE)
        self.assertEqual(service.current_status().code, "invalid")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

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
        page = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertContains(page, "Never (lifetime)")


class ExpiryWarningTests(LicenseTestCase):
    def test_warns_when_expiry_is_near_but_still_works(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=5))
        status = service.current_status()
        self.assertTrue(status.ok)
        self.assertIn("expires on", status.warn)
        self.assertEqual(status.days_left, 5)

    def test_banner_is_shown_once_a_day(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=3))
        first = self.client.get(reverse("admin:fileindex_fileindex_changelist"))
        self.assertTrue(any("expires on" in m for m in self.messages(first)))
        second = self.client.get(reverse("admin:fileindex_fileindex_changelist"))
        self.assertFalse(any("expires on" in m for m in self.messages(second)))

    def test_no_warning_when_far_from_expiry(self):
        self.store_token(expires=protocol.utcnow() + timedelta(days=200))
        self.assertEqual(service.current_status().warn, "")


# ------------------------------------------------------------------ server se baat
class ActivationFlowTests(LicenseTestCase):
    def post(self, name, data=None, user=None):
        if user:
            self.client.force_login(user)
        return self.client.post(reverse(name), data or {}, follow=True)

    def test_activating_through_the_page(self):
        r = self.post("admin:licensing_activate", {"key": "  exfn abcde fghjk lmnpq rstuv "})
        self.assertTrue(any("License activated" in m for m in self.messages(r)))
        self.assertContains(r, "ACTIVE")
        self.assertContains(r, "Ravi Traders")
        state = LicenseState.get()
        self.assertEqual(state.license_key, GOOD_KEY)
        self.assertTrue(service.current_status().ok)

    def test_sent_data_contains_only_the_agreed_fields(self):
        self.server.calls.clear()
        service.activate(GOOD_KEY)
        action, payload = self.server.calls[0]
        self.assertEqual(action, "activate")
        self.assertEqual(set(payload), {"key", "machine_id", "machine_name", "usage"})
        self.assertEqual(set(payload["usage"]), {"searches_total", "scans_total"})

    def test_wrong_key_shows_the_server_message_and_stays_blocked(self):
        r = self.post("admin:licensing_activate", {"key": "EXFN-AAAAA-AAAAA-AAAAA-AAAAA"})
        self.assertTrue(any("not valid" in m for m in self.messages(r)))
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

    def test_only_a_superuser_can_change_the_license(self):
        r = self.post("admin:licensing_activate", {"key": GOOD_KEY}, user=self.staff)
        self.assertTrue(any("administrator" in m for m in self.messages(r)))
        self.assertEqual(LicenseState.get().token, "")
        self.assertEqual(self.server.calls, [])

    def test_staff_can_view_the_license_page_but_sees_no_forms(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Sign in to your account")

    def test_actions_only_accept_post(self):
        self.assertEqual(self.client.get(reverse("admin:licensing_activate")).status_code, 405)

    def test_what_is_sent_is_explained_on_the_page(self):
        r = self.client.get(reverse("admin:licensing_licensestate_changelist"))
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
        self.assertEqual((action, payload["usage"]), ("check", {"searches_total": 7, "scans_total": 2}))
        self.assertNotEqual(LicenseState.get().token, old)
        self.assertIsNotNone(LicenseState.get().last_check_at)

    def test_revoked_license_is_blocked_at_the_next_check(self):
        self.activate_ok()
        self.server.error = (403, "revoked", "Payment failed. Contact support.")
        self.assertFalse(service.check_now().ok)
        service.invalidate()
        status = service.current_status()
        self.assertEqual((status.code, status.ok), ("revoked", False))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        page = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertContains(page, "DISABLED")
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


class TrialTests(LicenseTestCase):
    def setUp(self):
        super().setUp()
        self.server.license_type = "trial"
        self.server.expires = lambda now: now + timedelta(days=14)

    def test_trial_unlocks_the_app_without_a_key(self):
        result = service.start_trial()
        self.assertTrue(result.ok, result.message)
        status = service.current_status(force=True)
        self.assertEqual((status.ok, status.license_type), (True, "trial"))
        self.assertTrue(LicenseState.get().trial_started)
        self.assertEqual(LicenseState.get().license_key, "")

    def test_trial_check_uses_the_trial_endpoint_and_expiry_blocks(self):
        service.start_trial()
        self.server.calls.clear()
        service.check_now()
        self.assertEqual(self.server.calls[0][0], "trial")
        self.server.error = (403, "expired", "Your free trial has ended.")
        service.check_now()
        self.assertEqual(service.current_status(force=True).code, "expired")

    def test_trial_is_not_offered_on_the_page_but_the_ended_trial_message_works(self):
        page = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertNotContains(page, "Start free trial")       # ab plan seller deta hai (account ke baad)
        service.start_trial()
        LicenseState.objects.update(token="", license_key="")        # token gaya, par trial ho chuka hai
        service.invalidate()
        self.server.error = (403, "expired", "Your free trial has ended.")
        self.assertIn("trial has ended", service.start_trial().message)

    def test_trial_then_buying_a_key(self):
        service.start_trial()
        self.server.license_type = "yearly"
        self.server.expires = lambda now: now + timedelta(days=365)
        self.assertTrue(service.activate(GOOD_KEY).ok)
        self.assertEqual(service.current_status(force=True).license_type, "yearly")


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
            self.client.post(reverse("bulk_search"), {"numbers": "9856325417"})
        from fileindex.scanner import scan_lock
        self.addCleanup(lambda: scan_lock.locked() and scan_lock.release())      # run_scan nakli hai, lock wo chhodta nahi
        with mock.patch("fileindex.views.run_scan"):
            self.client.post(reverse("start_scan"), {"location": "__custom__", "custom_path": os.path.dirname(__file__)})
        state = LicenseState.get()
        self.assertEqual(state.searches_total, 3)
        self.assertEqual(state.scans_total, 1)

    def test_counting_never_breaks_a_search(self):
        self.activate_ok()
        with mock.patch.object(LicenseState.objects, "filter", side_effect=RuntimeError("db down")), \
                self.assertLogs("licensing.service", level="ERROR"):
            service.record_usage("search")           # exception bahar nahi aani chahiye


class AccountFlowTests(LicenseTestCase):
    """Email + password account: seller plan deta hai. Password app me kabhi save nahi hota."""

    def post(self, name, data=None):
        return self.client.post(reverse(name), data or {})

    def register(self, **kw):
        data = dict(name="Ravi", email="Ravi@Example.com", password="Str0ng-Pass-77", confirm="Str0ng-Pass-77")
        data.update(kw)
        return self.post("admin:licensing_register", data)

    def test_new_account_waits_for_approval_and_the_app_stays_closed(self):
        self.server.pending = True
        r = self.register()
        self.assertTrue(any("waiting for approval" in m for m in self.messages(r)))
        status = service.current_status(force=True)
        self.assertEqual((status.code, status.ok), ("pending", False))
        state = LicenseState.get()
        self.assertEqual((state.account_email, state.device_token), ("ravi@example.com", "device-secret-1"))
        self.assertEqual(state.token, "")
        page = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertContains(page, "WAITING FOR APPROVAL")
        self.assertContains(page, "location.reload")                 # page khud refresh hota hai
        self.assertRedirects(self.client.get(reverse("admin:index")), reverse("admin:licensing_licensestate_changelist"),
                             fetch_redirect_response=False)

    def test_password_is_sent_to_the_server_but_never_saved_here(self):
        self.server.pending = True
        self.register()
        self.assertEqual(self.server.calls[0][1]["password"], "Str0ng-Pass-77")
        saved = " ".join(str(v) for v in LicenseState.get().__dict__.values())
        self.assertNotIn("Str0ng-Pass-77", saved)
        service.check_now()
        self.assertNotIn("password", self.server.calls[-1][1])
        self.assertEqual(self.server.calls[-1][0], "account_check")
        self.assertEqual(self.server.calls[-1][1]["device_token"], "device-secret-1")

    def test_seller_gives_a_plan_and_the_next_check_opens_the_app(self):
        self.server.pending = True
        self.register()
        self.server.pending = False
        self.server.license_type = "monthly"
        result = service.check_now()
        self.assertTrue(result.ok, result.message)
        status = service.current_status(force=True)
        self.assertEqual((status.code, status.ok, status.license_type), ("active", True, "monthly"))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertContains(self.client.get(reverse("admin:licensing_licensestate_changelist")), "ravi@example.com")

    def test_sign_in_with_an_approved_account_activates_at_once(self):
        r = self.post("admin:licensing_login", {"email": "ravi@example.com", "password": "Str0ng-Pass-77"})
        self.assertTrue(any("Signed in" in m for m in self.messages(r)))
        self.assertTrue(service.current_status(force=True).ok)

    def test_wrong_password_saves_nothing(self):
        self.server.error = (401, "bad_login", "Wrong email or password.")
        r = self.post("admin:licensing_login", {"email": "ravi@example.com", "password": "nope"})
        self.assertTrue(any("Wrong email or password" in m for m in self.messages(r)))
        state = LicenseState.get()
        self.assertEqual((state.device_token, state.token, state.account_email), ("", "", ""))

    def test_mismatched_confirm_never_reaches_the_server(self):
        r = self.register(confirm="Different-Pass-1")
        self.assertTrue(any("do not match" in m for m in self.messages(r)))
        self.assertEqual(self.server.calls, [])

    def test_blocked_by_seller_shows_on_the_next_check(self):
        self.register()
        service.invalidate()
        self.assertTrue(service.current_status(force=True).ok)
        self.server.error = (403, "revoked", "This account has been disabled. Please contact support.")
        self.assertFalse(service.check_now().ok)
        self.assertEqual(service.current_status(force=True).code, "revoked")

    def test_sign_out_releases_the_pc_and_asks_to_sign_in_again(self):
        self.register()
        r = self.post("admin:licensing_deactivate")
        self.assertEqual(self.server.calls[-1][0], "logout")
        self.assertTrue(any("signed out" in m for m in self.messages(r)))
        state = LicenseState.get()
        self.assertEqual((state.device_token, state.account_email, state.token), ("", "", ""))
        self.assertEqual(service.current_status(force=True).code, "unlicensed")

    def test_sign_out_needs_internet_so_the_seat_is_really_freed(self):
        self.register()
        self.server.unreachable = True
        self.assertFalse(service.deactivate().ok)
        self.assertEqual(LicenseState.get().device_token, "device-secret-1")

    def test_revoked_device_must_sign_in_again(self):
        self.register()
        self.server.error = (409, "not_activated", "This PC is not signed in. Please sign in again.")
        service.check_now()
        self.assertEqual(service.current_status(force=True).code, "not_activated")

    def test_only_a_superuser_can_register_or_sign_in(self):
        self.client.force_login(self.staff)
        self.register()
        self.post("admin:licensing_login", {"email": "a@b.co", "password": "x"})
        self.assertEqual(self.server.calls, [])


class PendingBackgroundTests(LicenseTestCase):
    def setUp(self):
        super().setUp()
        service._state["next_bg_check"] = 0.0
        self.server.pending = True
        service.register("Ravi", "ravi@example.com", "Str0ng-Pass-77")
        service.invalidate()

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

    def test_pending_account_is_rechecked_within_minutes_not_a_day(self):
        LicenseState.objects.update(last_check_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(len(self.run_maybe()), 1)

    def test_pending_account_is_not_hammered(self):
        LicenseState.objects.update(last_check_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.run_maybe(), [])

    def test_blocked_pages_trigger_the_pending_check(self):
        with mock.patch.object(service, "maybe_background_check") as spy:
            self.client.get(reverse("admin:index"))
        spy.assert_called_once()


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
        self.assertEqual(self.client.get(reverse("admin:fileindex_bulksearch_changelist")).status_code, 200)
        self.assertEqual(self.client.post(reverse("bulk_search"), {"numbers": "9856325417"}).status_code, 200)

    def test_license_page_says_checks_are_off(self):
        r = self.client.get(reverse("admin:licensing_licensestate_changelist"))
        self.assertContains(r, "NOT ENFORCED")

    def test_sidebar_has_the_license_link(self):
        self.assertContains(self.client.get(reverse("admin:index")), "License &amp; Activation")


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
