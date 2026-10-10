"""Ek-click update ('Update now') ke tests.  Chalane ke liye:  python manage.py test search.test_install"""
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import update_check
from .test_updates import GOOD, UpdateTestCase

DRIVE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
EXE = b"MZ" + b"\x90\x00" * 5000                     # Windows program ki tarah 'MZ' se shuru
EXE_SHA = hashlib.sha256(EXE).hexdigest()


class FileServer:
    """Setup.exe ki nakal: jo body do wahi deta hai."""

    def __init__(self):
        outer = self
        self.body, self.status, self.hits = EXE, 200, 0

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/ExcelFinder-Setup-1.2.0.exe"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class DirectDownloadUrlTests(SimpleTestCase):
    def test_drive_share_links_become_direct_download_links(self):
        expected = f"https://drive.usercontent.google.com/download?id={DRIVE_ID}&export=download&confirm=t"
        for link in (f"https://drive.google.com/file/d/{DRIVE_ID}/view?usp=sharing",
                     f"https://drive.google.com/file/d/{DRIVE_ID}/view",
                     f"https://drive.google.com/uc?id={DRIVE_ID}&export=download",
                     f"https://drive.google.com/open?id={DRIVE_ID}"):
            self.assertEqual(update_check.direct_download_url(link), expected, link)

    def test_other_links_are_left_alone(self):
        for link in ("https://example.com/ExcelFinder-Setup.exe", "https://drive.google.com/drive/folders/abc",
                     "https://drive.google.com/file/d/x/view",                      # id bahut chhoti
                     "https://evil.example/file/d/" + DRIVE_ID + "/view"):           # Drive nahi
            self.assertEqual(update_check.direct_download_url(link), link, link)


class InstallTestCase(UpdateTestCase):
    def setUp(self):
        super().setUp()
        self.files = FileServer()
        self.addCleanup(self.files.close)
        self.data = Path(self.tmp.name)
        for override in (override_settings(DATA_DIR=self.data, UPDATE_CAN_INSTALL=True),):
            override.enable()
            self.addCleanup(override.disable)
        update_check._install.update(state="idle", percent=0, message="")
        self.launched = []
        for patcher in (mock.patch.object(update_check, "_launch", lambda path: self.launched.append(Path(path))),
                        mock.patch.object(update_check, "_exit_soon")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.exit_soon = update_check._exit_soon

    def release(self, **kw):
        latest = {**GOOD, "download_url": self.files.url, "sha256": EXE_SHA}
        latest.update(kw)
        self.server.set(latest)
        return update_check.check_now()

    def run_worker(self):
        update_check._install_worker(update_check._read()["latest"])
        return update_check.install_status()


class BannerTests(InstallTestCase):
    def test_update_now_button_shows_for_a_verifiable_release(self):
        self.release()
        page = self.page()
        self.assertContains(page, "Update now")
        self.assertContains(page, reverse("update_install"))
        self.assertContains(page, "Download")                    # link bhi rehta hai (bharosa na ho toh browser se)

    def test_no_button_without_a_checksum(self):
        self.release(sha256="")
        self.assertNotContains(self.page(), "Update now")

    @override_settings(UPDATE_CAN_INSTALL=False)
    def test_no_button_outside_the_installed_windows_app(self):
        self.release()
        self.assertNotContains(self.page(), "Update now")
        self.assertContains(self.page(), "Download")

    def test_no_release_published_yet_is_not_an_error(self):
        self.server.set({"none": True})
        result = update_check.check_now()
        self.assertTrue(result.ok)
        self.assertIn("latest version", result.message)
        self.assertIsNone(update_check.current_notice())


class StartInstallTests(InstallTestCase):
    def test_refuses_when_there_is_nothing_newer(self):
        self.release(version="1.0.0")
        self.assertFalse(update_check.start_install().ok)

    def test_refuses_without_a_checksum_or_outside_windows_app(self):
        self.release(sha256="")
        self.assertIn("Download link", update_check.start_install().message)
        self.release()
        with override_settings(UPDATE_CAN_INSTALL=False):
            self.assertIn("Download link", update_check.start_install().message)

    def test_starts_one_background_download_only(self):
        self.release()
        started = []

        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                started.append((target, args))

            def start(self):
                pass

        with mock.patch.object(update_check.threading, "Thread", FakeThread):
            self.assertTrue(update_check.start_install().ok)
            self.assertFalse(update_check.start_install().ok)         # doosri baar nahi
        self.assertEqual(len(started), 1)
        self.assertEqual(update_check.install_status()["state"], "downloading")


class WorkerTests(InstallTestCase):
    def test_good_download_is_verified_and_the_installer_is_started(self):
        self.release()
        status = self.run_worker()
        self.assertEqual(status["state"], "installing")
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.launched[0].read_bytes(), EXE)
        self.assertEqual(self.launched[0].parent, self.data / "updates")
        update_check._exit_soon.assert_called_once()

    def test_wrong_checksum_is_never_installed_and_the_file_is_deleted(self):
        self.release(sha256="b" * 64)
        status = self.run_worker()
        self.assertEqual(status["state"], "error")
        self.assertIn("checksum", status["message"])
        self.assertEqual(self.launched, [])
        self.assertEqual(list((self.data / "updates").iterdir()), [])
        update_check._exit_soon.assert_not_called()

    def test_a_web_page_instead_of_the_installer_is_refused(self):
        self.files.body = b"<html>Google Drive can't scan this file for viruses</html>"
        self.release(sha256=hashlib.sha256(self.files.body).hexdigest())
        status = self.run_worker()
        self.assertEqual(status["state"], "error")
        self.assertIn("web page", status["message"])
        self.assertEqual(self.launched, [])

    def test_empty_and_huge_downloads_are_refused(self):
        self.files.body = b""
        self.release(sha256=hashlib.sha256(b"").hexdigest())
        self.assertEqual(self.run_worker()["state"], "error")
        self.files.body = EXE
        self.release()
        with mock.patch.object(update_check, "MAX_INSTALLER_BYTES", 100):
            status = self.run_worker()
        self.assertIn("unexpectedly large", status["message"])
        self.assertEqual(self.launched, [])

    def test_server_error_gives_a_friendly_message(self):
        self.files.status = 404
        self.release()
        with self.assertLogs("search.update_check", "WARNING"):
            status = self.run_worker()
        self.assertEqual(status["state"], "error")
        self.assertIn("download failed", status["message"])
        self.assertEqual(self.launched, [])

    def test_old_installers_are_cleaned_up(self):
        old = self.data / "updates"
        old.mkdir()
        (old / "ExcelFinder-Setup-0.9.0.exe").write_bytes(b"old")
        self.release()
        self.run_worker()
        self.assertEqual([p.name for p in old.iterdir()], ["ExcelFinder-Setup-1.2.0.exe"])

    def test_unexpected_crash_is_reported_not_raised(self):
        self.release()
        with mock.patch.object(update_check, "_download", side_effect=RuntimeError("boom")), self.assertLogs("search.update_check", "ERROR"):
            self.assertEqual(self.run_worker()["state"], "error")


class LaunchTests(SimpleTestCase):
    def test_installer_runs_silently_and_detached(self):
        with mock.patch.object(update_check.subprocess, "Popen") as popen:
            update_check._launch(Path("C:/x/ExcelFinder-Setup-1.2.0.exe"))
        args = popen.call_args.args[0]
        self.assertTrue(args[0].endswith("ExcelFinder-Setup-1.2.0.exe"))
        self.assertEqual(args[1:], ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])


class ViewTests(InstallTestCase):
    def test_install_endpoint_starts_and_status_reports(self):
        self.release()
        with mock.patch.object(update_check.threading, "Thread") as thread:
            r = self.client.post(reverse("update_install"))
        self.assertEqual(r.json()["ok"], True)
        thread.return_value.start.assert_called_once()
        status = self.client.get(reverse("update_status")).json()
        self.assertEqual(status["state"], "downloading")

    def test_install_needs_post_and_a_staff_login(self):
        self.assertEqual(self.client.get(reverse("update_install")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("update_install")).status_code, 302)
        self.assertEqual(self.client.get(reverse("update_status")).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(reverse("update_install")).status_code, 302)

    def test_install_with_nothing_to_install_says_so(self):
        r = self.client.post(reverse("update_install"))
        self.assertFalse(r.json()["ok"])
