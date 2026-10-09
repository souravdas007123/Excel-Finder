"""Is PC ki pehchaan (hash). License isi PC se bandhta hai."""
import hashlib
import os
import platform
import subprocess
import sys
import uuid
from functools import lru_cache


def _raw_id():
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, "MachineGuid")[0]
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.exists(path):
                value = open(path).read().strip()
                if value:
                    return value
    except Exception:
        pass
    return f"{uuid.getnode():x}-{platform.node()}"      # aakhri raasta


@lru_cache(maxsize=1)
def machine_id():
    """64 hex akshar. Testing ke liye EXCEL_FINDER_MACHINE_ID (installed .exe me ye kaam nahi karta)."""
    override = os.environ.get("EXCEL_FINDER_MACHINE_ID")
    if override and not getattr(sys, "frozen", False):
        return hashlib.sha256(override.encode()).hexdigest()
    return hashlib.sha256(("excel-finder|" + _raw_id()).encode()).hexdigest()


def machine_name():
    return platform.node()[:100]
