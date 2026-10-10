"""'Naya version available hai' ka notice aur ek-click update.

App ek chhoti JSON padhta hai: license server ka `/api/v1/latest` (seller admin panel ke 'App releases' me version daalta
hai) ya website ki `latest.json`. Customer ko banner me 'Update now' dikhta hai: app naya Setup.exe download karta hai,
SHA-256 se jaanchta hai, phir chalakar khud band ho jata hai aur installer purane ko badal deta hai (data bacha rehta hai).
Bina SHA-256 wale release me (ya Windows installed app ke bahar) sirf 'Download' link dikhta hai.

latest.json ka namuna:
  {"version": "1.2.0", "released": "2026-11-01", "download_url": "https://example.com/download",
   "sha256": "...", "notes": ["Faster scans", "Bug fixes"], "min_version": "1.0.0"}
`min_version`: isse purana version ho toh banner laal ("update zaruri") dikhta hai.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings

from .version import VERSION

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHECK_INTERVAL = timedelta(hours=24)
RETRY_SECONDS = 300            # check fail ho toh itne ke baad hi dobara koshish
MAX_BYTES = 64 * 1024          # latest.json is se bada ho toh mana (galat / nakli server se bachav)
MAX_NOTES, MAX_NOTE_LEN = 8, 200

_state = {"next_attempt": 0.0}
_lock = threading.Lock()


@dataclass
class Result:
    ok: bool
    message: str


# ------------------------------------------------------------------ version
def parse_version(text):
    if not isinstance(text, str) or not VERSION_RE.match(text.strip()):
        return None
    parts = [int(p) for p in text.strip().split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


# ------------------------------------------------------------------ settings
def enabled():
    return bool(getattr(settings, "UPDATE_CHECK_URL", ""))


def _cache_path():
    return Path(getattr(settings, "UPDATE_CACHE_FILE", Path(settings.DATA_DIR) / "update_info.json"))


def _check_url():
    url = (getattr(settings, "UPDATE_CHECK_URL", "") or "").strip()
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("The update address must start with https://")
    return url


def _safe_download_url(url):
    parts = urllib.parse.urlparse(url if isinstance(url, str) else "")
    if parts.scheme == "https" and parts.netloc:
        return url
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"):    # sirf testing
        return url
    raise ValueError("download_url must be an https:// link")


DRIVE_FILE_RE = re.compile(r"^/file/d/([A-Za-z0-9_-]{10,})")
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def direct_download_url(url):
    """Google Drive ka share link ('.../file/d/ID/view') seedhe download link me badlo; baaki links jaise ke taise."""
    parts = urllib.parse.urlparse(url)
    if parts.hostname in ("drive.google.com", "docs.google.com"):
        match = DRIVE_FILE_RE.match(parts.path)
        file_id = match.group(1) if match else (urllib.parse.parse_qs(parts.query).get("id") or [""])[0]
        if DRIVE_ID_RE.match(file_id):
            return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    return url


def validate_manifest(data):
    """latest.json ko saaf karke wapas do, ya ValueError. Is par bharosa nahi: sab kuch jaancha jata hai."""
    if not isinstance(data, dict):
        raise ValueError("latest.json must be an object")
    version = data.get("version")
    if parse_version(version) is None:
        raise ValueError("version is missing or not like 1.2.0")
    notes = data.get("notes", [])
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        raise ValueError("notes must be a list")
    sha = str(data.get("sha256", "")).strip().lower()
    min_version = data.get("min_version")
    return {
        "version": version.strip(),
        "released": str(data.get("released", ""))[:30],
        "download_url": _safe_download_url(data.get("download_url")),
        "sha256": sha if SHA256_RE.match(sha) else "",
        "notes": [str(n).strip()[:MAX_NOTE_LEN] for n in notes[:MAX_NOTES] if str(n).strip()],
        "min_version": min_version.strip() if parse_version(min_version) else "",
    }


def fetch_latest(timeout=6):
    request = urllib.request.Request(_check_url(), headers={"User-Agent": f"ExcelFinder/{VERSION}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("latest.json is too large")
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict) and data.get("none"):       # seller ne abhi koi release nahi daali
        return None
    return validate_manifest(data)


# ------------------------------------------------------------------ saved state (chhoti JSON file, database nahi)
def _read():
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)             # beech me band hone par aadhi file nahi bachti
    except OSError:
        logger.exception("Could not save the update info")


def _now():
    return datetime.now(timezone.utc)


def check_now():
    """Abhi check karo (button ya background). Result me insaan ke liye message."""
    state = _read()
    try:
        latest = fetch_latest()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        state.update(checked_at=_now().isoformat(), error="Could not reach the update server.")
        _write(state)
        return Result(False, "Could not reach the update server. Please check your internet connection.")
    except (ValueError, UnicodeDecodeError) as exc:
        state.update(checked_at=_now().isoformat(), error="The update information could not be read.")
        _write(state)
        logger.warning("Bad update info: %s", exc)
        return Result(False, "The update information could not be read. Please try again later.")
    state.update(checked_at=_now().isoformat(), latest=latest, error="")
    _write(state)
    if latest is not None and is_newer(latest["version"], VERSION):
        return Result(True, f"Version {latest['version']} is available (you have {VERSION}).")
    return Result(True, f"You have the latest version ({VERSION}).")


def _due():
    try:
        checked = datetime.fromisoformat(_read().get("checked_at", ""))
    except ValueError:
        return True
    return _now() - checked >= CHECK_INTERVAL


def maybe_background_check():
    """Har admin page par chalta hai, par roz mein ek hi baar asli check (alag thread me, page ruke bina)."""
    if not enabled() or time.monotonic() < _state["next_attempt"]:
        return
    with _lock:
        if time.monotonic() < _state["next_attempt"]:
            return
        _state["next_attempt"] = time.monotonic() + RETRY_SECONDS
    if _due():
        threading.Thread(target=_worker, daemon=True).start()


def _worker():
    try:
        check_now()
    except Exception:
        logger.exception("Background update check failed")


# ------------------------------------------------------------------ banner ke liye
def current_notice():
    """Banner ka data, ya None (update nahi / customer ne 'Dismiss' kiya / feature band)."""
    if not enabled():
        return None
    state = _read()
    latest = state.get("latest")
    if not isinstance(latest, dict) or not is_newer(latest.get("version"), VERSION):
        return None
    critical = bool(latest.get("min_version")) and is_newer(latest["min_version"], VERSION)
    if state.get("dismissed_version") == latest["version"] and not critical:
        return None
    return {**latest, "current": VERSION, "critical": critical,
            "one_click": can_install() and bool(latest.get("sha256")), "installing": _install["state"] in BUSY}


def dismiss(version):
    state = _read()
    state["dismissed_version"] = str(version)[:20]
    _write(state)


# ------------------------------------------------------------------ ek-click update (download + verify + install)
BUSY = ("downloading", "installing")
MAX_INSTALLER_BYTES = 1536 * 1024 * 1024
CHUNK = 256 * 1024
_install = {"state": "idle", "percent": 0, "message": ""}
_install_lock = threading.Lock()


def can_install():
    """Sirf Windows par bane hue (installed) app me. Dusri jagah sirf Download link."""
    return bool(getattr(settings, "UPDATE_CAN_INSTALL", sys.platform == "win32" and getattr(sys, "frozen", False)))


def install_status():
    return dict(_install)


def _set(state, message, percent=None):
    _install["state"], _install["message"] = state, message
    if percent is not None:
        _install["percent"] = percent


def start_install():
    """'Update now' dabane par: download alag thread me shuru. Result me turant jawab."""
    latest = _read().get("latest") if enabled() else None
    if not isinstance(latest, dict) or not is_newer(latest.get("version"), VERSION):
        return Result(False, "There is no newer version to install.")
    if not can_install():
        return Result(False, "One-click update works only in the installed Windows app. Please use the Download link.")
    if not SHA256_RE.match(str(latest.get("sha256", ""))):
        return Result(False, "This release cannot be installed automatically. Please use the Download link.")
    with _install_lock:
        if _install["state"] in BUSY:
            return Result(False, "The update is already in progress.")
        _set("downloading", "Starting the download...", 0)
    threading.Thread(target=_install_worker, args=(latest,), daemon=True).start()
    return Result(True, "Downloading the update...")


def _updates_dir():
    folder = Path(settings.DATA_DIR) / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():                       # purane adhure / puraane installers hatao
        try:
            old.unlink()
        except OSError:
            pass
    return folder


def _download(url, target, progress):
    """File utarkar uska SHA-256 return karo. Bahut bada / Google ka web page ho toh ValueError."""
    request = urllib.request.Request(direct_download_url(url), headers={"User-Agent": f"ExcelFinder/{VERSION}"})
    digest, done = hashlib.sha256(), 0
    with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        first = True
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            if first:
                first = False
                if not chunk.startswith(b"MZ"):        # Windows program nahi: shayad Drive ka web page
                    raise ValueError("The download link did not give the installer file (it gave a web page). "
                                     "Check that the file is shared as 'Anyone with the link', or use the Download link.")
            done += len(chunk)
            if done > MAX_INSTALLER_BYTES:
                raise ValueError("The download is unexpectedly large, so it was stopped.")
            digest.update(chunk)
            out.write(chunk)
            progress(done, total)
    if done == 0:
        raise ValueError("The download was empty.")
    return digest.hexdigest()


def _launch(path):
    flags = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True, creationflags=flags)


def _exit_soon():
    """Installer purani files badal sake isliye app band: 2 second baad (jawab bhej dene ke baad)."""
    threading.Timer(2.0, os._exit, args=(0,)).start()


def _install_worker(latest):
    target = None
    try:
        folder = _updates_dir()
        target = folder / f"ExcelFinder-Setup-{latest['version']}.exe"

        def progress(done, total):
            percent = int(done * 100 / total) if total else 0
            _set("downloading", f"Downloading... {percent}%" if total else f"Downloading... {done // (1024 * 1024)} MB", percent)

        actual = _download(latest["download_url"], target, progress)
        if actual != latest["sha256"]:
            target.unlink(missing_ok=True)
            raise ValueError("The downloaded file does not match the expected checksum, so it was NOT installed. "
                             "Please try again or contact support.")
        _set("installing", "Installing the new version. The app will close and open again by itself...", 100)
        _launch(target)
        _exit_soon()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Update download failed: %s", exc)
        _set("error", "The download failed. Please check your internet connection and try again.")
    except ValueError as exc:
        _set("error", str(exc))
    except Exception:
        logger.exception("Update install failed")
        _set("error", "The update could not be installed. Please use the Download link.")
    if _install["state"] == "error" and target is not None:
        try:
            Path(target).unlink(missing_ok=True)
        except OSError:
            pass
