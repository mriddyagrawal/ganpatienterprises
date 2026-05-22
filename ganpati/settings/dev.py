"""
Development settings — used when running locally on the owner's computer.
"""

from .base import *  # noqa: F401,F403
from .base import env

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="dev-only-insecure-do-not-use-in-prod-or-on-a-public-host",
)

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    # `.local` (with the leading dot) matches any *.local mDNS hostname —
    # convenient for LAN access from another device on the same Wi-Fi
    # (e.g. `mriddy.local:8000` from a phone). Override via the
    # DJANGO_ALLOWED_HOSTS env var when you want a tighter set or need to
    # add a literal LAN IP (`192.168.x.y`) for clients that don't resolve
    # mDNS. Pair with `python manage.py runserver 0.0.0.0:8000` so the
    # server actually listens on the LAN interface (not just 127.0.0.1).
    default=["localhost", "127.0.0.1", "0.0.0.0", ".local"],
)

# Helpful console-print emails in dev rather than sending real ones.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
