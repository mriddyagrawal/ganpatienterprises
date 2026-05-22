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
    default=["localhost", "127.0.0.1", "0.0.0.0"],
)

# Helpful console-print emails in dev rather than sending real ones.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
