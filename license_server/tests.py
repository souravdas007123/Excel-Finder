"""License server ke tests.

Chalane ke liye (project folder se):
    LICENSE_SERVER_DEBUG=1 python manage.py test license_server --settings=license_server.server_settings
    (Windows PowerShell:  $env:LICENSE_SERVER_DEBUG=1; python manage.py test license_server --settings=license_server.server_settings)

Customer wali app ke `python manage.py test` me ye skip ho jate hain.
"""
import unittest
from io import StringIO

from django.apps import apps

if not apps.is_installed("license_server"):
    raise unittest.SkipTest("License server tests: --settings=license_server.server_settings ke saath chalao")

import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from licensing import protocol

from . import core
from .models import Activation, License

PRIVATE, PUBLIC = protocol.generate_keypair()
PC1, PC2, PC3 = ("a" * 64, "b" * 64, "c" * 64)


@override_settings(LICENSE_PRIVATE_KEY=PRIVATE, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])   # tez tests
class ServerTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def call(self, action, **data):
        return self.client.post(f"/api/v1/{action}", json.dumps(data), content_type="application/json")

    def make(self, **kw):
        defaults = dict(customer_name="Ravi Traders", license_type="yearly")
        defaults.update(kw)
        return core.create_license(**defaults)

    def activate(self, key, machine=PC1, **extra):
        return self.call("activate", key=key, machine_id=machine, machine_name="OFFICE-PC", app_version="1.0", **extra)

    def status_of(self, response, machine=PC1, now=None):
        return protocol.evaluate(response.json()["token"], PUBLIC, machine, now or timezone.now())


class ActivationTests(ServerTestCase):
    def test_yearly_activation_returns_a_token_the_app_accepts(self):
        lic, key = self.make()
        r = self.activate(key)
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        status = protocol.evaluate(j["token"], PUBLIC, PC1)
        self.assertTrue(status.ok, status.message)
        self.assertEqual((status.license_type, status.customer), ("yearly", "Ravi Traders"))
        self.assertIn(status.days_left, (364, 365))
        lic.refresh_from_db()
        self.assertIsNotNone(lic.first_activated_at)
        self.assertAlmostEqual((lic.expires_at - lic.first_activated_at).days, 365, delta=1)

    def test_token_is_bound_to_the_pc(self):
        _, key = self.make()
        token = self.activate(key).json()["token"]
        self.assertEqual(protocol.evaluate(token, PUBLIC, PC2).code, "wrong_machine")

    def test_lifetime_never_expires(self):
        _, key = self.make(license_type="lifetime", customer_name="Sita")
        r = self.activate(key)
        status = self.status_of(r, now=timezone.now() + timedelta(days=20 * 365))
        # 20 saal baad bhi expire nahi, bas online check maangega (offline grace khatam)
        self.assertIsNone(status.expires)
        self.assertNotEqual(status.code, "expired")
        self.assertTrue(self.status_of(r).ok)

    def test_key_typed_in_any_style_is_accepted(self):
        _, key = self.make()
        messy = "  " + key.lower().replace("-", " ") + " "
        self.assertEqual(self.activate(messy).status_code, 200)
        self.assertEqual(self.activate(key.replace("-", "")).status_code, 200)

    def test_wrong_key_is_rejected(self):
        self.make()
        for bad in ("", "nonsense", "EXFN-AAAAA-AAAAA-AAAAA-AAAAA"):
            r = self.activate(bad)
            self.assertEqual((r.status_code, r.json()["error"]), (404, "invalid_key"), bad)

    def test_revoked_license_cannot_activate(self):
        lic, key = self.make()
        lic.revoked, lic.revoked_reason = True, "Payment failed"
        lic.save()
        r = self.activate(key)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "revoked"))
        self.assertEqual(r.json()["message"], "Payment failed")

    def test_expired_license_cannot_activate(self):
        lic, key = self.make()
        self.activate(key)
        License.objects.filter(pk=lic.pk).update(expires_at=timezone.now() - timedelta(days=1))
        r = self.activate(key)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "expired"))

    def test_machine_limit_and_deactivation(self):
        _, key = self.make(max_machines=1)
        self.assertEqual(self.activate(key, PC1).status_code, 200)
        self.assertEqual(self.activate(key, PC1).status_code, 200)            # wahi PC dobara: theek
        r = self.activate(key, PC2)
        self.assertEqual((r.status_code, r.json()["error"]), (409, "machine_limit"))
        self.assertEqual(self.call("deactivate", key=key, machine_id=PC1).json(), {"ok": True, "deactivated": True})
        self.assertEqual(self.activate(key, PC2).status_code, 200)            # seat khali hui
        self.assertEqual(self.activate(key, PC1).status_code, 409)            # ab PC1 ko seat nahi

    def test_team_license_allows_several_pcs(self):
        _, key = self.make(max_machines=2)
        self.assertEqual([self.activate(key, m).status_code for m in (PC1, PC2, PC3)], [200, 200, 409])

    def test_bad_requests(self):
        _, key = self.make()
        self.assertEqual(self.call("activate", key=key, machine_id="short").json()["error"], "bad_request")
        self.assertEqual(self.client.post("/api/v1/activate", "not json", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/activate", "[1,2]", content_type="application/json").status_code, 400)
        self.assertEqual(self.call("nope").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/activate").status_code, 405)

    def test_ping(self):
        self.assertTrue(self.client.get("/api/v1/ping").json()["ok"])

    @override_settings(LICENSE_PRIVATE_KEY="")
    def test_server_without_signing_key_reports_a_clear_error(self):
        _, key = self.make()
        r = self.activate(key)
        self.assertEqual((r.status_code, r.json()["error"]), (500, "server_error"))


class CheckTests(ServerTestCase):
    def test_check_refreshes_token_and_stores_usage(self):
        lic, key = self.make()
        self.activate(key)
        r = self.call("check", key=key, machine_id=PC1, app_version="1.1",
                      usage={"searches_total": 12, "scans_total": 3})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.status_of(r).ok)
        act = Activation.objects.get(license=lic)
        self.assertEqual((act.searches_total, act.scans_total, act.app_version), (12, 3, "1.1"))
        self.call("check", key=key, machine_id=PC1, usage={"searches_total": 5})            # reinstall: ginti ghat nahi sakti
        act.refresh_from_db()
        self.assertEqual(act.searches_total, 12)

    def test_usage_values_are_sanitised(self):
        lic, key = self.make()
        self.activate(key)
        self.call("check", key=key, machine_id=PC1, usage={"searches_total": "junk", "scans_total": -5})
        act = Activation.objects.get(license=lic)
        self.assertEqual((act.searches_total, act.scans_total), (0, 0))
        self.assertEqual(self.call("check", key=key, machine_id=PC1, usage="oops").status_code, 200)

    def test_check_needs_an_activation(self):
        _, key = self.make()
        r = self.call("check", key=key, machine_id=PC1)
        self.assertEqual((r.status_code, r.json()["error"]), (409, "not_activated"))

    def test_revoking_blocks_the_next_check(self):
        lic, key = self.make()
        self.activate(key)
        License.objects.filter(pk=lic.pk).update(revoked=True)
        r = self.call("check", key=key, machine_id=PC1)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "revoked"))

    def test_expiry_is_reported_on_check(self):
        lic, key = self.make()
        self.activate(key)
        License.objects.filter(pk=lic.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "expired")

    def test_check_by_never_goes_past_expiry(self):
        lic, key = self.make(offline_grace_days=30)
        self.activate(key)
        License.objects.filter(pk=lic.pk).update(expires_at=timezone.now() + timedelta(days=3))
        status = self.status_of(self.call("check", key=key, machine_id=PC1))
        self.assertLessEqual(status.check_by, status.expires)

    def test_deactivated_pc_must_activate_again(self):
        _, key = self.make()
        self.activate(key)
        self.call("deactivate", key=key, machine_id=PC1)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "not_activated")
        self.assertFalse(self.call("deactivate", key=key, machine_id=PC1).json()["deactivated"])


class RenewalTests(ServerTestCase):
    def test_extend_adds_to_remaining_time(self):
        lic, key = self.make()
        self.activate(key)
        lic.refresh_from_db()
        before = lic.expires_at
        core.extend(lic, 365)
        self.assertAlmostEqual((lic.expires_at - before).days, 365, delta=0)

    def test_extend_an_expired_license_starts_from_today(self):
        lic, key = self.make()
        self.activate(key)
        License.objects.filter(pk=lic.pk).update(expires_at=timezone.now() - timedelta(days=100))
        lic.refresh_from_db()
        core.extend(lic, 365)
        self.assertAlmostEqual((lic.expires_at - timezone.now()).days, 365, delta=1)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).status_code, 200)    # phir chalu

    def test_extend_before_first_activation_adds_to_the_duration(self):
        lic, key = self.make()
        core.extend(lic, 365)
        self.assertEqual(lic.duration_days, 730)

    def test_extend_does_nothing_for_lifetime(self):
        lic, _ = self.make(license_type="lifetime")
        core.extend(lic, 365)
        self.assertIsNone(lic.expires_at)

    def test_rotate_key_stops_the_old_key(self):
        lic, old = self.make()
        new = core.rotate_key(lic)
        self.assertEqual(self.activate(old).json()["error"], "invalid_key")
        self.assertEqual(self.activate(new).status_code, 200)

    def test_get_license_lookup(self):
        lic, key = self.make()
        self.assertEqual(core.get_license(key[-5:]), lic)
        self.assertEqual(core.get_license(str(lic.id)[:8]), lic)
        with self.assertRaises(LookupError):
            core.get_license("zzzzz")


class RateLimitTests(ServerTestCase):
    @override_settings(RATE_LIMIT_PER_MINUTE=3)
    def test_too_many_requests_are_blocked(self):
        codes = [self.activate("EXFN-AAAAA-AAAAA-AAAAA-AAAAA").status_code for _ in range(5)]
        self.assertEqual(codes, [404, 404, 404, 429, 429])


class AdminTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("seller", "s@example.com", "pw-12345")
        self.client.force_login(self.admin)

    def test_creating_a_license_shows_the_key_once_and_stores_only_the_hash(self):
        r = self.client.post(reverse("admin:license_server_license_add"), {
            "customer_name": "Amit Stores", "customer_email": "a@x.com", "notes": "", "license_type": "yearly",
            "max_machines": 1, "duration_days": "", "offline_grace_days": 14,
            "activations-TOTAL_FORMS": 0, "activations-INITIAL_FORMS": 0, "activations-MIN_NUM_FORMS": 0,
            "activations-MAX_NUM_FORMS": 0,
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        text = r.content.decode()
        start = text.index("EXFN-")
        key = protocol.normalize_key(text[start:start + 29])
        self.assertTrue(key, "key message me nahi dikhi")
        lic = License.objects.get(customer_name="Amit Stores")
        self.assertEqual(lic.duration_days, 365)
        self.assertEqual(lic.key_hash, protocol.hash_key(key))
        self.assertNotIn(key, lic.key_hash)
        self.assertEqual(self.activate(key).status_code, 200)

    def test_changelist_and_detail_pages_open(self):
        lic, key = self.make()
        self.activate(key)
        r = self.client.get(reverse("admin:license_server_license_changelist"))
        self.assertContains(r, "Ravi Traders")
        self.assertContains(r, "1/1")
        self.assertEqual(self.client.get(reverse("admin:license_server_license_change", args=[lic.pk])).status_code, 200)

    def run_action(self, action, *licenses):
        return self.client.post(reverse("admin:license_server_license_changelist"),
                                {"action": action, "_selected_action": [str(lic.pk) for lic in licenses]}, follow=True)

    def test_revoke_and_restore_actions(self):
        lic, key = self.make()
        self.activate(key)
        self.run_action("revoke_selected", lic)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "revoked")
        self.run_action("unrevoke_selected", lic)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).status_code, 200)

    def test_renew_action(self):
        lic, key = self.make()
        self.activate(key)
        lic.refresh_from_db()
        before = lic.expires_at
        self.run_action("add_365_days", lic)
        lic.refresh_from_db()
        self.assertEqual((lic.expires_at - before).days, 365)

    def test_generate_key_action_shows_a_new_key_and_the_old_one_stops(self):
        lic, old = self.make()
        r = self.run_action("key_yearly", lic)
        text = r.content.decode()
        new = protocol.normalize_key(text[text.index("EXFN-"):][:29])
        self.assertTrue(new)
        self.assertNotEqual(new, old)
        self.assertEqual(self.activate(old).status_code, 404)
        self.assertEqual(self.activate(new).status_code, 200)

    def test_freeing_a_seat_from_the_admin(self):
        lic, key = self.make()
        self.activate(key)
        Activation.objects.filter(license=lic).update(active=False)
        self.assertEqual(self.activate(key, PC2).status_code, 200)

    def test_pages_need_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:license_server_license_changelist")).status_code, 302)


class CommandTests(ServerTestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_create_license_command_prints_a_working_key(self):
        out = self.run_cmd("create_license", "--customer", "Ravi", "--type", "lifetime", "--machines", "2")
        key = protocol.normalize_key(out.split("KEY:")[1].split()[0])
        self.assertTrue(key)
        lic = License.objects.get()
        self.assertEqual((lic.license_type, lic.max_machines, lic.duration_days), ("lifetime", 2, None))
        self.assertEqual(self.activate(key).status_code, 200)

    def test_list_extend_and_revoke_commands(self):
        lic, key = self.make(customer_name="Meena")
        self.activate(key)
        self.assertIn("Meena", self.run_cmd("list_licenses"))
        self.assertIn("now valid until", self.run_cmd("extend_license", key[-5:], "--days", "30"))
        self.assertIn("REVOKED", self.run_cmd("revoke_license", key[-5:], "--reason", "chargeback"))
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["message"], "chargeback")
        self.assertIn("restored", self.run_cmd("revoke_license", key[-5:], "--undo"))
        with self.assertRaises(CommandError):
            self.run_cmd("revoke_license", "nothing")

    def test_keygen_prints_a_usable_keypair(self):
        out = self.run_cmd("keygen")
        private = out.split("PRIVATE")[1].split("\n")[1].strip()
        public = out.split("PUBLIC")[1].split("\n")[1].strip()
        token = protocol.sign_token({"v": 1, "x": 1}, private)
        self.assertEqual(protocol.verify_token(token, public)["x"], 1)


# ------------------------------------------------------------------ customer ki request, key, reset code, usage
class RequestTests(ServerTestCase):
    def register(self, **kw):
        data = dict(name="Ravi Kumar", email="Ravi@Example.com", machine_id=PC1)
        data.update(kw)
        return self.call("register", **data)

    def test_request_makes_a_waiting_for_key_row(self):
        r = self.register()
        self.assertEqual((r.status_code, r.json()["ok"]), (200, True))
        lic = License.objects.get()
        self.assertEqual((lic.customer_name, lic.customer_email, lic.license_type, lic.state()),
                         ("Ravi Kumar", "ravi@example.com", "pending", "pending"))
        self.assertEqual(lic.activations.count(), 0)

    def test_asking_again_does_not_make_a_second_row(self):
        self.register()
        self.register(email="ravi@example.COM", machine_id=PC2, name="Someone else")
        self.assertEqual(License.objects.count(), 1)
        self.assertEqual(License.objects.get().customer_name, "Ravi Kumar")        # purani row nahi badalti

    def test_bad_name_and_email_are_refused(self):
        self.assertEqual(self.register(email="not-an-email").json()["error"], "bad_email")
        self.assertEqual(self.register(name="  ").json()["error"], "bad_name")
        self.assertEqual(self.register(name=None).json()["error"], "bad_name")
        self.assertFalse(License.objects.exists())

    def test_no_password_is_involved(self):
        self.register(password="should-be-ignored")
        self.assertFalse(hasattr(License.objects.get(), "password_hash"))

    def test_the_hidden_key_of_a_request_cannot_be_used(self):
        self.register()
        r = self.activate("EXFN-AAAAA-AAAAA-AAAAA-AAAAA")
        self.assertEqual(r.status_code, 404)


class KeyFlowTests(ServerTestCase):
    """Customer ne request ki -> seller ne 'Generate key' kiya -> customer ne key daali -> 1 PC par chalu."""

    def setUp(self):
        super().setUp()
        self.call("register", name="Ravi", email="ravi@example.com", machine_id=PC1)
        self.lic = License.objects.get()

    def test_monthly_key_works_for_30_days_from_first_use(self):
        key = core.issue_key(self.lic, "monthly")
        status = self.status_of(self.activate(key))
        self.assertEqual((status.ok, status.license_type), (True, "monthly"))
        self.assertAlmostEqual(status.days_left, 30, delta=1)

    def test_yearly_and_lifetime_keys(self):
        status = self.status_of(self.activate(core.issue_key(self.lic, "yearly")))
        self.assertAlmostEqual(status.days_left, 365, delta=1)
        lic = License.objects.get()
        core.release_pcs(lic)
        status = self.status_of(self.activate(core.issue_key(lic, "lifetime"), machine=PC2), machine=PC2)
        self.assertEqual((status.license_type, status.expires), ("lifetime", None))

    def test_the_clock_starts_at_first_use_not_when_the_key_is_made(self):
        core.issue_key(self.lic, "monthly")
        self.lic.refresh_from_db()
        self.assertIsNone(self.lic.expires_at)
        self.assertEqual(self.lic.state(), "unused")

    def test_key_works_on_one_pc_only_until_the_pc_is_freed(self):
        key = core.issue_key(self.lic, "yearly")
        self.assertEqual(self.activate(key).status_code, 200)
        r = self.activate(key, machine=PC2)
        self.assertEqual((r.status_code, r.json()["error"]), (409, "machine_limit"))
        self.assertEqual(core.release_pcs(self.lic), 1)
        self.assertEqual(self.activate(key, machine=PC2).status_code, 200)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "not_activated")

    def test_new_key_for_the_same_customer_replaces_the_old_one(self):
        old = core.issue_key(self.lic, "monthly")
        new = core.issue_key(self.lic, "yearly")
        self.assertEqual(self.activate(old).status_code, 404)
        self.assertEqual(self.activate(new).status_code, 200)

    def test_cancel_blocks_at_the_next_check_and_unblock_restores(self):
        key = core.issue_key(self.lic, "yearly")
        self.activate(key)
        License.objects.filter(pk=self.lic.pk).update(revoked=True, revoked_reason="Refund")
        r = self.call("check", key=key, machine_id=PC1)
        self.assertEqual((r.status_code, r.json()["error"], r.json()["message"]), (403, "revoked", "Refund"))
        License.objects.filter(pk=self.lic.pk).update(revoked=False)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).status_code, 200)

    def test_renewing_after_expiry(self):
        key = core.issue_key(self.lic, "monthly")
        self.activate(key)
        License.objects.filter(pk=self.lic.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "expired")
        core.extend(License.objects.get(pk=self.lic.pk), 30)
        self.assertEqual(self.call("check", key=key, machine_id=PC1).status_code, 200)

    def test_issue_key_rejects_unknown_plans(self):
        with self.assertRaises(ValueError):
            core.issue_key(self.lic, "trial")


class UsageTests(ServerTestCase):
    def test_files_indexed_is_the_latest_count_and_the_other_counters_only_grow(self):
        lic, key = self.make()
        self.activate(key, usage={"files_indexed": 120, "searches_total": 5, "scans_total": 2})
        self.call("check", key=key, machine_id=PC1, usage={"files_indexed": 90, "searches_total": 3, "scans_total": 1})
        a = lic.activations.get()
        self.assertEqual((a.files_indexed, a.searches_total, a.scans_total), (90, 5, 2))     # index saaf ho toh files ghat sakti hain

    def test_bad_files_indexed_is_ignored(self):
        lic, key = self.make()
        self.activate(key, usage={"files_indexed": 50})
        self.call("check", key=key, machine_id=PC1, usage={"files_indexed": "many"})
        self.assertEqual(lic.activations.get().files_indexed, 50)               # samajh na aaye toh purani ginti rehti hai
        self.call("check", key=key, machine_id=PC1, usage={"files_indexed": -5})
        self.assertEqual(lic.activations.get().files_indexed, 0)                 # negative = 0


class ResetCodeTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.lic, _key = self.make(customer_email="ravi@example.com")

    def reset(self, code, email="ravi@example.com"):
        return self.call("reset", email=email, code=code)

    def test_code_works_once(self):
        code = core.make_reset_code(self.lic)
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertTrue(self.reset(code).json()["ok"])
        r = self.reset(code)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_code"))

    def test_code_is_forgiving_about_case_and_dashes(self):
        code = core.make_reset_code(self.lic)
        self.assertTrue(self.reset(" " + code.lower().replace("-", " ") + " ").json()["ok"])

    def test_code_expires_after_24_hours(self):
        code = core.make_reset_code(self.lic)
        License.objects.filter(pk=self.lic.pk).update(reset_expires=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.reset(code).json()["error"], "bad_code")

    def test_wrong_code_wrong_email_and_no_code(self):
        code = core.make_reset_code(self.lic)
        for bad in ("AAAA-AAAA", "", "x"):
            self.assertEqual(self.reset(bad).json()["error"], "bad_code")
        self.assertEqual(self.reset(code, email="other@example.com").json()["error"], "bad_code")
        self.assertEqual(self.call("reset", email="not-an-email", code=code).json()["error"], "bad_email")
        self.assertTrue(self.reset(code).json()["ok"])                       # galat koshishon se asli code kharab nahi hua

    def test_nothing_issued_means_nothing_works(self):
        self.assertEqual(self.reset("AAAA-AAAA").json()["error"], "bad_code")

    def test_guessing_is_locked_out(self):
        code = core.make_reset_code(self.lic)
        for _ in range(core.RESET_FAIL_LIMIT):
            self.assertEqual(self.reset("AAAA-AAAA").json()["error"], "bad_code")
        r = self.reset(code)                                                  # sahi code bhi ab nahi chalega
        self.assertEqual((r.status_code, r.json()["error"]), (429, "too_many_attempts"))
        cache.clear()
        self.assertTrue(self.reset(code).json()["ok"])

    def test_a_new_code_replaces_the_old_one(self):
        old = core.make_reset_code(self.lic)
        new = core.make_reset_code(self.lic)
        self.assertEqual(self.reset(old).json()["error"], "bad_code")
        self.assertTrue(self.reset(new).json()["ok"])

    def test_the_code_is_stored_hashed(self):
        code = core.make_reset_code(self.lic)
        self.lic.refresh_from_db()
        self.assertNotIn(code.replace("-", ""), self.lic.reset_code_hash)
        self.assertEqual(len(self.lic.reset_code_hash), 64)


class CustomerAdminTests(ServerTestCase):
    """Seller ka 'Customers' page: kaun, kaun sa plan, kitni files, key banana, cancel, PC, reset code."""

    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345")
        self.client.force_login(self.admin)
        self.call("register", name="Ravi Kumar", email="ravi@example.com", machine_id=PC1)
        self.lic = License.objects.get()
        self.url = reverse("admin:license_server_license_changelist")

    def act(self, action, lic=None):
        return self.client.post(self.url, {"action": action, "_selected_action": [str((lic or self.lic).pk)]}, follow=True)

    def key_in(self, response):
        text = response.content.decode()
        return protocol.normalize_key(text[text.index("EXFN-"):][:29])

    def test_list_shows_the_customer_waiting_for_a_key(self):
        r = self.client.get(self.url)
        for text in ("Ravi Kumar", "ravi@example.com", "Waiting for key", "Files indexed"):
            self.assertContains(r, text)

    def test_generate_key_actions_set_the_plan_and_show_a_working_key_once(self):
        for action, plan in (("key_monthly", "monthly"), ("key_yearly", "yearly"), ("key_lifetime", "lifetime")):
            r = self.act(action)
            key = self.key_in(r)
            self.assertContains(r, "ravi@example.com")
            self.assertContains(r, "will NOT be shown again")
            self.assertEqual(self.status_of(self.activate(key, machine=PC1)).license_type, plan)
            core.release_pcs(self.lic)
        self.assertNotContains(self.client.get(self.url), key)                  # key dobara nahi dikhti

    def test_list_shows_plan_pcs_files_and_usage_after_activation(self):
        key = self.key_in(self.act("key_yearly"))
        self.activate(key, usage={"files_indexed": 4321, "searches_total": 17, "scans_total": 3})
        r = self.client.get(self.url)
        self.assertContains(r, "Yearly")
        self.assertContains(r, "1/1")
        self.assertContains(r, "4321")
        self.assertContains(r, ">17<")

    def test_cancel_and_restore(self):
        key = self.key_in(self.act("key_yearly"))
        self.activate(key)
        self.act("revoke_selected")
        self.assertEqual(self.call("check", key=key, machine_id=PC1).json()["error"], "revoked")
        self.act("unrevoke_selected")
        self.assertEqual(self.call("check", key=key, machine_id=PC1).status_code, 200)

    def test_renew_actions(self):
        key = self.key_in(self.act("key_monthly"))
        self.activate(key)
        self.lic.refresh_from_db()
        before = self.lic.expires_at
        self.act("add_30_days")
        self.act("add_365_days")
        self.lic.refresh_from_db()
        self.assertEqual((self.lic.expires_at - before).days, 395)

    def test_free_the_pc(self):
        key = self.key_in(self.act("key_yearly"))
        self.activate(key)
        self.assertEqual(self.activate(key, machine=PC2).status_code, 409)
        self.act("free_the_pc")
        self.assertEqual(self.activate(key, machine=PC2).status_code, 200)

    def test_reset_code_action_shows_a_code_that_works(self):
        r = self.act("reset_code")
        match = re.search(r"<code[^>]*>([A-Z2-9]{4}-[A-Z2-9]{4})</code>", r.content.decode())
        self.assertTrue(match)
        self.assertContains(r, "ravi@example.com")
        self.assertTrue(self.call("reset", email="ravi@example.com", code=match.group(1)).json()["ok"])

    def test_one_action_for_several_customers_gives_each_their_own_key(self):
        self.call("register", name="Amit", email="amit@example.com", machine_id=PC2)
        both = License.objects.all()
        r = self.client.post(self.url, {"action": "key_monthly", "_selected_action": [str(x.pk) for x in both]}, follow=True)
        self.assertContains(r, "Ravi Kumar")
        self.assertContains(r, "Amit")
        self.assertEqual(sorted(License.objects.values_list("license_type", flat=True)), ["monthly", "monthly"])

    def test_add_form_and_detail_pages_open(self):
        self.assertEqual(self.client.get(reverse("admin:license_server_license_change", args=[self.lic.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:license_server_license_add")).status_code, 200)

    def test_pages_need_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)



# ------------------------------------------------------------------ app updates (seller 'App releases' me version daalta hai)
class ReleaseTests(ServerTestCase):
    def release(self, version="1.1.0", **kw):
        from .models import AppRelease
        data = dict(version=kw.pop("version", version), download_url="https://drive.google.com/file/d/ABC123/view?usp=sharing",
                    sha256="a" * 64, notes="Faster scans\nBug fixes")
        data.update(kw)
        release = AppRelease(**data)
        release.full_clean()
        release.save()
        return release

    def latest(self):
        return self.client.get("/api/v1/latest")

    def test_no_release_yet(self):
        self.assertEqual(self.latest().json(), {"none": True})

    def test_latest_published_version_wins_by_number_not_by_date(self):
        self.release("1.9.0")
        self.release("1.10.0")
        self.release("2.0.0", published=False)
        data = self.latest().json()
        self.assertEqual(data["version"], "1.10.0")
        self.assertEqual(data["notes"], ["Faster scans", "Bug fixes"])
        self.assertEqual(data["sha256"], "a" * 64)

    def test_manifest_passes_the_apps_own_validator(self):
        from search import update_check
        self.release("1.1.0", min_version="1.0.0")
        clean = update_check.validate_manifest(self.latest().json())
        self.assertEqual((clean["version"], clean["min_version"]), ("1.1.0", "1.0.0"))

    def test_bad_input_is_rejected_in_the_admin_form(self):
        from django.core.exceptions import ValidationError
        for bad in (dict(version="one"), dict(download_url="http://example.com/a.exe"), dict(sha256="xyz"),
                    dict(min_version="old")):
            with self.assertRaises(ValidationError, msg=str(bad)):
                self.release(**{"version": "3.0.0", **bad})

    def test_release_admin_pages_open(self):
        admin_user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.force_login(admin_user)
        self.release()
        self.assertContains(self.client.get(reverse("admin:license_server_apprelease_changelist")), "1.1.0")
        self.assertEqual(self.client.get(reverse("admin:license_server_apprelease_add")).status_code, 200)

    def test_latest_is_rate_limited(self):
        with override_settings(RATE_LIMIT_PER_MINUTE=2):
            codes = [self.latest().status_code for _ in range(4)]
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[-1], 429)
