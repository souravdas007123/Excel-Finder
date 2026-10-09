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


@override_settings(LICENSE_PRIVATE_KEY=PRIVATE)
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


class TrialTests(ServerTestCase):
    def test_trial_starts_without_a_key(self):
        r = self.call("trial", machine_id=PC1, machine_name="HOME", app_version="1.0")
        self.assertEqual(r.status_code, 200)
        status = self.status_of(r)
        self.assertEqual((status.ok, status.license_type), (True, "trial"))
        self.assertIn(status.days_left, (13, 14))

    def test_one_trial_per_pc(self):
        self.call("trial", machine_id=PC1)
        self.call("trial", machine_id=PC1)
        self.assertEqual(License.objects.filter(license_type="trial").count(), 1)
        self.call("trial", machine_id=PC2)
        self.assertEqual(License.objects.filter(license_type="trial").count(), 2)

    def test_trial_cannot_be_restarted_after_it_ends(self):
        self.call("trial", machine_id=PC1)
        License.objects.filter(license_type="trial").update(expires_at=timezone.now() - timedelta(days=1))
        r = self.call("trial", machine_id=PC1)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "expired"))
        self.assertIn("trial has ended", r.json()["message"])
        self.assertEqual(License.objects.filter(license_type="trial").count(), 1)


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
        self.run_action("extend_one_year", lic)
        lic.refresh_from_db()
        self.assertEqual((lic.expires_at - before).days, 365)

    def test_new_key_action_shows_the_new_key(self):
        lic, old = self.make()
        r = self.run_action("issue_new_key", lic)
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
