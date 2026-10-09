"""License server se baat (HTTPS JSON). Sirf Python ki standard library use hoti hai."""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from search.version import VERSION


class ServerUnreachable(Exception):
    """Internet / server nahi mila (license ko block karne ki wajah nahi, bas check nahi ho paya)."""


def _base_url():
    url = (settings.LICENSE_SERVER_URL or "").strip().rstrip("/")
    if not url:
        raise ServerUnreachable("The license server address is not set in this build.")
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
        raise ServerUnreachable("The license server address must start with https://")
    return url


def call(action, payload, timeout=12):
    """(http_status, dict). Network ya server ki dikkat par ServerUnreachable."""
    url = f"{_base_url()}/api/v1/{action}"
    payload = dict(payload, app_version=VERSION)
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"ExcelFinder/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:            # server ne jawab diya (4xx / 5xx)
        try:
            data = json.loads(exc.read() or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict) or "error" not in data:
            data = {"ok": False, "error": "http_error", "message": f"The license server returned an error ({exc.code})."}
        return exc.code, data
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ServerUnreachable("Could not reach the license server. Please check your internet connection.") from exc
