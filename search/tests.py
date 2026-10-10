"""Project-level tests: pehli baar ka admin setup page, installer ke hisse (build config, launcher), installed-mode settings.

Chalane ke liye:  python manage.py test search
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from licensing import protocol, service
from . import middleware as first_run

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@override_settings(LICENSE_ENFORCED=True)
class AccountSetupTests(TestCase):
    """Bechne wali app: pehla page customer ka ACCOUNT banata hai (server par) aur wahi is PC ka login bhi hai."""

    def setUp(self):
        first_run.reset_first_run_cache()
        self.addCleanup(first_run.reset_first_run_cache)
        self.register = mock.patch.object(service, "register", return_value=service.Result(True, "ok"))
        self.signin = mock.patch.object(service, "login", return_value=service.Result(True, "ok"))
        self.register_mock, self.login_mock = self.register.start(), self.signin.start()
        self.addCleanup(self.register.stop)
        self.addCleanup(self.signin.stop)

    DATA = {"name": "Ravi", "email": "Ravi@Example.com", "password": "Str0ng-Pass-77", "confirm": "Str0ng-Pass-77"}

    def test_page_asks_for_name_email_and_password(self):
        r = self.client.get("/setup/")
        self.assertContains(r, "Create your account")
        for field in ('name="name"', 'name="email"', 'name="password"', 'name="confirm"'):
            self.assertContains(r, field)
        self.assertNotContains(r, 'name="username"')

    def test_signup_creates_the_server_account_and_the_local_login(self):
        r = self.client.post("/setup/", self.DATA)
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)
        self.register_mock.assert_called_once_with("Ravi", "ravi@example.com", "Str0ng-Pass-77")
        user = get_user_model().objects.get()
        self.assertEqual((user.username, user.email, user.is_superuser), ("ravi@example.com", "ravi@example.com", True))
        self.assertTrue(user.check_password("Str0ng-Pass-77"))
        self.assertIn("_auth_user_id", self.client.session)          # seedha login ho gaya

    def test_server_refusal_creates_no_local_user(self):
        self.register_mock.return_value = service.Result(False, "An account with this email already exists.")
        r = self.client.post("/setup/", self.DATA)
        self.assertContains(r, "already exists")
        self.assertFalse(get_user_model().objects.exists())

    def test_offline_gives_a_clear_message_and_creates_nothing(self):
        self.register_mock.return_value = service.Result(False, "Could not reach the license server. Please check your internet connection.")
        r = self.client.post("/setup/", self.DATA)
        self.assertContains(r, "internet connection")
        self.assertFalse(get_user_model().objects.exists())

    def test_bad_input_never_reaches_the_server(self):
        for change in ({"email": "nope"}, {"name": " "}, {"confirm": "Different-Pass-1"}, {"password": "short", "confirm": "short"},
                       {"password": "password", "confirm": "password"}):
            r = self.client.post("/setup/", {**self.DATA, **change})
            self.assertEqual(r.status_code, 200, change)
            self.assertContains(r, 'class="errorlist"')
        self.register_mock.assert_not_called()
        self.assertFalse(get_user_model().objects.exists())

    def test_existing_customer_can_sign_in_on_a_new_pc(self):
        page = self.client.get("/setup/?mode=signin")
        self.assertContains(page, "Sign in to Excel Finder")
        self.assertNotContains(page, 'name="confirm"')
        r = self.client.post("/setup/", {"mode": "signin", "email": "ravi@example.com", "password": "Str0ng-Pass-77"})
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)
        self.login_mock.assert_called_once_with("ravi@example.com", "Str0ng-Pass-77")
        self.register_mock.assert_not_called()
        self.assertTrue(get_user_model().objects.filter(username="ravi@example.com").exists())

    def test_wrong_password_on_sign_in_creates_nothing(self):
        self.login_mock.return_value = service.Result(False, "Wrong email or password.")
        r = self.client.post("/setup/", {"mode": "signin", "email": "ravi@example.com", "password": "nope-nope-1"})
        self.assertContains(r, "Wrong email or password")
        self.assertFalse(get_user_model().objects.exists())

    def test_setup_is_closed_once_an_account_exists(self):
        get_user_model().objects.create_superuser("owner", "o@example.com", "Str0ng-Pass-77")
        r = self.client.post("/setup/", self.DATA)
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)
        self.register_mock.assert_not_called()


class FirstRunSetupTests(TestCase):
    def setUp(self):
        first_run.reset_first_run_cache()
        self.addCleanup(first_run.reset_first_run_cache)

    def test_every_page_goes_to_setup_while_there_is_no_user(self):
        for path in ("/", "/admin/", "/admin/login/", "/admin/fileindex/bulksearch/"):
            r = self.client.get(path)
            self.assertRedirects(r, "/setup/", fetch_redirect_response=False, msg_prefix=path)
        self.assertEqual(self.client.get("/setup/").status_code, 200)

    def test_setup_page_shows_the_form(self):
        r = self.client.get("/setup/")
        self.assertContains(r, "Create account and continue")
        self.assertContains(r, 'value="admin"')

    def test_creating_the_account_signs_in_and_opens_the_app(self):
        r = self.client.post("/setup/", {"username": "owner", "password": "Str0ng-Pass-77", "confirm": "Str0ng-Pass-77"})
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)
        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(user.check_password("Str0ng-Pass-77"))
        self.assertEqual(self.client.get("/admin/").status_code, 200)         # sign-in ho chuka

    def test_bad_input_is_rejected_with_a_reason(self):
        cases = [
            ({"username": "owner", "password": "abcdefgh1", "confirm": "abcdefgh2"}, "do not match"),
            ({"username": "owner", "password": "password", "confirm": "password"}, "too common"),
            ({"username": "owner", "password": "short", "confirm": "short"}, "too short"),
            ({"username": "two words", "password": "Str0ng-Pass-77", "confirm": "Str0ng-Pass-77"}, "username"),
            ({"username": "", "password": "Str0ng-Pass-77", "confirm": "Str0ng-Pass-77"}, "username"),
        ]
        for data, expected in cases:
            r = self.client.post("/setup/", data)
            self.assertEqual(r.status_code, 200, data)
            self.assertIn(expected, r.content.decode().lower().replace("&#x27;", "'"), data)
        self.assertFalse(get_user_model().objects.exists())

    def test_setup_is_closed_once_an_account_exists(self):
        get_user_model().objects.create_superuser("owner", "", "Str0ng-Pass-77")
        self.assertRedirects(self.client.get("/setup/"), "/admin/", fetch_redirect_response=False)
        r = self.client.post("/setup/", {"username": "intruder", "password": "Str0ng-Pass-88", "confirm": "Str0ng-Pass-88"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(get_user_model().objects.count(), 1)                    # koi dusra admin nahi ban sakta

    def test_root_redirects_to_admin_after_setup(self):
        get_user_model().objects.create_superuser("owner", "", "Str0ng-Pass-77")
        self.assertRedirects(self.client.get("/"), "/admin/", fetch_redirect_response=False)


class BuildConfigTests(SimpleTestCase):
    SCRIPT = ROOT / "installer" / "make_build_config.py"

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(self.SCRIPT), *args], capture_output=True, text=True)

    def test_writes_a_config_the_app_can_read(self):
        private, public = protocol.generate_keypair()
        with tempfile.TemporaryDirectory() as tmp:
            key_file, out = Path(tmp) / "private.txt", Path(tmp) / "build_config.py"
            key_file.write_text(private + "\n")
            r = self.run_script("--server", "https://license.example.com/", "--private-key-file", str(key_file),
                                "--buy-url", "https://example.com/buy", "--support", "help@example.com", "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            config = load_module("generated_build_config", out)
        self.assertTrue(config.ENFORCED)
        self.assertEqual(config.SERVER_URL, "https://license.example.com")           # aakhir ka / hata diya
        self.assertEqual(config.PUBLIC_KEY, public)                                  # private se nikli public key
        self.assertNotIn(private, out.name)
        self.assertEqual((config.BUY_URL, config.SUPPORT), ("https://example.com/buy", "help@example.com"))

    def test_private_key_never_ends_up_in_the_config(self):
        private, _ = protocol.generate_keypair()
        with tempfile.TemporaryDirectory() as tmp:
            key_file, out = Path(tmp) / "private.txt", Path(tmp) / "build_config.py"
            key_file.write_text(private)
            self.run_script("--server", "https://x.example", "--private-key-file", str(key_file), "--out", str(out))
            self.assertNotIn(private, out.read_text())

    def test_refuses_insecure_server_and_bad_public_key(self):
        _, public = protocol.generate_keypair()
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "c.py")
            r = self.run_script("--server", "http://license.example.com", "--public-key", public, "--out", out)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("https://", r.stderr)
            self.assertEqual(self.run_script("--server", "http://127.0.0.1:8800", "--allow-http", "--public-key", public, "--out", out).returncode, 0)
            r = self.run_script("--server", "https://x.example", "--public-key", "not-a-key", "--out", out)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("public key", r.stderr)


class InstalledBuildSettingsTests(SimpleTestCase):
    """Build config ho toh license check customer band nahi kar sakta; installed mode me data customer ke folder me."""

    def settings_in_subprocess(self, code, **env):
        full = {**os.environ, "DJANGO_SETTINGS_MODULE": "search.settings", "PYTHONPATH": str(ROOT)}
        for k in list(full):
            if k.startswith(("EXCEL_FINDER", "DJANGO_")) and k != "DJANGO_SETTINGS_MODULE":
                full.pop(k)
        full.update(env)
        return subprocess.run([sys.executable, "-c", code], env=full, cwd=ROOT, capture_output=True, text=True)

    FAKE_BUILD = (
        "import sys, types\n"
        "m = types.ModuleType('licensing.build_config'); m.ENFORCED = True; m.SERVER_URL = 'https://lic.example'\n"
        "m.PUBLIC_KEY = 'PUB'; m.BUY_URL = ''; m.SUPPORT = ''\n"
        "sys.modules['licensing.build_config'] = m\n"
        "from django.conf import settings\n"
        "print(settings.LICENSE_ENFORCED, settings.LICENSE_SERVER_URL, settings.LICENSE_PUBLIC_KEY)\n")

    def test_dev_copy_is_not_enforced_unless_asked(self):
        code = "from django.conf import settings; print(settings.LICENSE_ENFORCED)"
        self.assertEqual(self.settings_in_subprocess(code).stdout.strip(), "False")
        self.assertEqual(self.settings_in_subprocess(code, EXCEL_FINDER_LICENSE_ENFORCED="1").stdout.strip(), "True")

    def test_environment_cannot_switch_off_an_installed_build(self):
        r = self.settings_in_subprocess(self.FAKE_BUILD, EXCEL_FINDER_LICENSE_ENFORCED="0",
                                        EXCEL_FINDER_LICENSE_SERVER="http://evil.example", EXCEL_FINDER_LICENSE_PUBLIC_KEY="EVIL")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "True https://lic.example PUB")

    def test_installed_mode_keeps_data_in_the_customers_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys; sys.frozen = True\n"
                    "from django.conf import settings\n"
                    "print(settings.DEBUG, settings.SESSION_COOKIE_SECURE, settings.DATABASES['default']['NAME'], "
                    "'whitenoise.middleware.WhiteNoiseMiddleware' in settings.MIDDLEWARE, settings.ALLOWED_HOSTS)\n")
            r = self.settings_in_subprocess(code, EXCEL_FINDER_DATA=tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            debug, secure, db, whitenoise, hosts = r.stdout.strip().split(" ", 4)
            self.assertEqual((debug, secure, whitenoise), ("False", "False", "True"))
            self.assertEqual(Path(db), Path(tmp) / "db.sqlite3")
            self.assertTrue((Path(tmp) / "secret.key").read_text().strip())
            self.assertIn("127.0.0.1", hosts)
            second = self.settings_in_subprocess(code, EXCEL_FINDER_DATA=tmp)       # key har baar wahi rehti hai
            self.assertEqual(second.returncode, 0)


class LauncherTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.launcher = load_module("excel_finder_launcher", ROOT / "launcher.py")

    def test_parse_args(self):
        self.assertEqual(self.launcher.parse_args([]), {"gui": True, "browser": True, "port": 8765})
        self.assertEqual(self.launcher.parse_args(["--no-gui", "--no-browser", "--port", "9001"]),
                         {"gui": False, "browser": False, "port": 9001})

    def test_pick_port_skips_busy_ports(self):
        import socket
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        self.assertFalse(self.launcher.port_is_free(port))
        chosen = self.launcher.pick_port(port)
        self.assertNotEqual(chosen, port)
        self.assertTrue(self.launcher.port_is_free(chosen))

    def test_already_running_detects_a_live_app_and_ignores_stale_files(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertIsNone(self.launcher.already_running(folder))                  # port.txt hi nahi
            (folder / "port.txt").write_text(str(server.server_address[1]))
            self.assertEqual(self.launcher.already_running(folder), f"http://127.0.0.1:{server.server_address[1]}/")
            (folder / "port.txt").write_text("1")                                     # purani file, koi sun nahi raha
            self.assertIsNone(self.launcher.already_running(folder))
            (folder / "port.txt").write_text("junk")
            self.assertIsNone(self.launcher.already_running(folder))

    def test_data_dir_follows_the_installed_mode_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EXCEL_FINDER_DATA": tmp}), mock.patch.object(sys, "frozen", True, create=True):
                self.assertEqual(self.launcher.data_dir(), Path(tmp))
        self.assertEqual(self.launcher.data_dir(), ROOT)                              # source copy: project folder

    def test_output_is_redirected_when_there_is_no_console(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            self.launcher.redirect_output_if_no_console(Path(tmp))
            print("hello")                                                           # crash nahi hona chahiye
            self.assertTrue((Path(tmp) / "launcher.log").exists())
            sys.stdout.close()
