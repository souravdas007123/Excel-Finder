"""License server ki settings. Ye app SIRF seller ke paas chalta hai (customer ko nahi diya jata).

Chalane ke liye (project folder se):
    python manage.py migrate --settings=license_server.server_settings
    python manage.py runserver 8800 --settings=license_server.server_settings

Environment variables (server par zaroori):
    LICENSE_SERVER_SECRET_KEY    lamba random secret (DEBUG band ho toh zaroori)
    LICENSE_SERVER_ALLOWED_HOSTS jaise: license.example.com
    LICENSE_PRIVATE_KEY          signing key (ya file license_server/private_key.txt). Banane ke liye: manage.py keygen
    LICENSE_SERVER_DEBUG=1       sirf apne computer par testing ke liye
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent


def _env_bool(name, default=False):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


DEBUG = _env_bool("LICENSE_SERVER_DEBUG", False)
SECRET_KEY = os.environ.get("LICENSE_SERVER_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set LICENSE_SERVER_SECRET_KEY (or LICENSE_SERVER_DEBUG=1 for local testing).")
    SECRET_KEY = "dev-only-license-server-key"

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("LICENSE_SERVER_ALLOWED_HOSTS", "").split(",") if h.strip()]
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.environ.get("LICENSE_SERVER_CSRF_ORIGINS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "license_server",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "license_server.server_urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "license_server.server_wsgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ.get("LICENSE_SERVER_DB", str(BASE_DIR / "license_server.sqlite3")),
}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "license_server_static"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:   # https ke peeche chalao (nginx / hosting). Sirf https par cookies
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# ---- License rules ----
# Signing (private) key. Isko KISI ko mat dena aur git me mat daalna. 'manage.py keygen' se banti hai.
LICENSE_PRIVATE_KEY = os.environ.get("LICENSE_PRIVATE_KEY", "").strip()
if not LICENSE_PRIVATE_KEY and (_HERE / "private_key.txt").exists():
    LICENSE_PRIVATE_KEY = (_HERE / "private_key.txt").read_text().strip()

MONTHLY_DAYS = 30          # monthly plan kitne din ka
YEARLY_DAYS = 365          # yearly license kitne din ka (pehli activation se)
OFFLINE_GRACE_DAYS = 14    # server se bina check kiye app kitne din chal sakta hai
RATE_LIMIT_PER_MINUTE = 40   # ek IP se ek minute me itni requests (guess / spam rokne ke liye)
TRUST_PROXY_HEADERS = _env_bool("LICENSE_SERVER_TRUST_PROXY", False)   # nginx ke peeche ho toh 1
