"""
Development settings — used when running locally on the owner's computer.
"""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0"],
)

# Helpful console-print emails in dev rather than sending real ones.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
