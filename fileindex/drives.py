import os
import string


def get_scan_locations():
    """Is computer par jo drives sach me maujood hain wahi deta hai: {key: {'label': ..., 'path': ...}}"""
    return _windows_drives() if os.name == "nt" else _unix_locations()


def _windows_drives():
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()   # bitmask: bit 0 = A:, bit 2 = C: ...
    kinds = {2: "Removable", 3: "Local Disk", 4: "Network"}   # CD/DVD, RAM disk wagairah skip

    locations = {}
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        path = f"{letter}:\\"
        drive_type = kernel32.GetDriveTypeW(path)
        kind = kinds.get(drive_type)
        if kind is None:
            continue
        if drive_type == 2 and not os.path.isdir(path):   # khali card reader / USB slot
            continue
        locations[letter.lower()] = {"label": f"{letter}: Drive ({kind})", "path": path}
    return locations


def _unix_locations():
    home = os.path.expanduser("~")
    return {
        "home": {"label": f"Home ({home})", "path": home},
        "root": {"label": "/ (Root)", "path": "/"},
    }