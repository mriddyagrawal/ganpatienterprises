"""
Base settings shared across all environments.
Environment-specific overrides live in `dev.py` (and later `prod.py`).
"""

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core security
# ---------------------------------------------------------------------------

# SECRET_KEY is intentionally NOT set here. Each environment-specific
# settings module (dev.py, prod.py) is responsible for assigning it.
# Dev provides a clearly-insecure fallback; prod must require it via env.

DEBUG = env.bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Third-party admin theme — must be listed BEFORE django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",

    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.humanize",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "django_htmx",
    "tailwind",
    "theme",  # the Tailwind theme app, scaffolded via `manage.py tailwind init`

    # First-party
    "accounts",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "ganpati.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ganpati.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# SQLite for V1 local dev. When public hosting is set up (futureplans.md #3),
# swap by setting DATABASE_URL to a Postgres connection string.

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    ),
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# ---------------------------------------------------------------------------
# I18n / time
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Tailwind
# ---------------------------------------------------------------------------

TAILWIND_APP_NAME = "theme"
INTERNAL_IPS = ["127.0.0.1"]

# ---------------------------------------------------------------------------
# Notifications (Phase 6)
# ---------------------------------------------------------------------------

# Which provider class to instantiate. "telegram" today; "twilio_sms",
# "whatsapp_cloud" planned. Set to "console" in dev/tests to skip the
# network and just log the body.
NOTIFICATION_PROVIDER = env("NOTIFICATION_PROVIDER", default="console")

# Telegram bot credentials. Required only when NOTIFICATION_PROVIDER ==
# "telegram"; missing values surface as a clear startup error from the
# provider factory rather than a silent 401 at send time.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_API_BASE = env(
    "TELEGRAM_API_BASE", default="https://api.telegram.org"
)

# Per-attempt timeout when calling the provider's HTTP API. Kept short
# so the dispatcher cron doesn't pile up on a slow upstream — failures
# get retried via the chain.
NOTIFICATION_TIMEOUT_SECONDS = env.int(
    "NOTIFICATION_TIMEOUT_SECONDS", default=5
)

# Backoff schedule, in seconds, indexed by attempt_number (1 → first
# retry delay). After the last entry, the chain is abandoned.
NOTIFICATION_RETRY_BACKOFF_SECONDS = [60, 300, 1800, 7200, 43200]
