"""'Naya version available hai' ka notice.

App sirf ek chhoti file padhta hai: website par rakhi `latest.json` (website/build_site.py banata hai). Kuch download ya
install apne aap NAHI hota: customer ko banner me link dikhta hai, wo khud naya Setup.exe download karke chalata hai.

latest.json ka namuna:
  {"version": "1.2.0", "released": "2026-11-01", "download_url": "https://example.com/download",
   "sha256": "...", "notes": ["Faster scans", "Bug fixes"], "min_version": "1.0.0"}
`min_version`: isse purana version ho toh banner laal ("update zaruri") dikhta hai.
"""
import json
import logging
import os
import re
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
    return validate_manifest(json.loads(raw.decode("utf-8")))


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
    if is_newer(latest["version"], VERSION):
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
    return {**latest, "current": VERSION, "critical": critical}


def dismiss(version):
    state = _read()
    state["dismissed_version"] = str(version)[:20]
    _write(state)
