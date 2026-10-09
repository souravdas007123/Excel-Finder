# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Excel Finder ko ek folder (dist/ExcelFinder/) me bandhta hai. Isse Inno Setup installer banta hai.
# Build: pyinstaller installer/ExcelFinder.spec --noconfirm   (ya installer/build_windows.ps1)
import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)      # collect_submodules apne packages (search, fileindex, licensing) import kar sake
CONSOLE = os.environ.get("EXCEL_FINDER_CONSOLE") == "1"     # debugging: console window ke saath build

datas = collect_data_files("django")                           # admin templates / static / locale
datas += [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "fileindex", "templates"), os.path.join("fileindex", "templates")),
    (os.path.join(ROOT, "licensing", "templates"), os.path.join("licensing", "templates")),
    (os.path.join(ROOT, "staticfiles"), "staticfiles"),        # python manage.py collectstatic ka output
]

# Django in modules ko naam (string) se import karta hai (settings, middleware, urls, migrations), isliye PyInstaller ko
# dikhte nahi: unhe yahan seedhe likhna zaruri hai.
hiddenimports = (
    collect_submodules("fileindex")        # migrations bhi
    + collect_submodules("licensing")
    + collect_submodules("search")
    + collect_submodules("django.contrib")
    + ["search", "search.settings", "search.urls", "search.wsgi", "search.middleware", "search.setup_views",
       "search.version", "licensing.middleware", "licensing.build_config",
       "python_calamine", "waitress", "whitenoise", "whitenoise.middleware", "openpyxl", "cryptography",
       "cryptography.hazmat.primitives.asymmetric.ed25519"]
    + [m for m in ("xlrd",) if importlib.util.find_spec(m)]     # sirf agar installed ho (.xls ka purana reader, fallback)
)

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["license_server", "pytest", "IPython"],          # seller wala server customer ke paas nahi jata
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ExcelFinder",
    console=CONSOLE,
    icon=os.path.join(SPECPATH, "icon.ico") if os.path.exists(os.path.join(SPECPATH, "icon.ico")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ExcelFinder")
