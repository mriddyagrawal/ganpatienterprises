"""Settings-driven provider selection.

`get_provider()` is called once per dispatcher run; instances are cached
per (provider-name, settings tuple) so repeated calls don't re-instantiate.
"""
from __future__ import annotations

from functools import lru_cache

from django.conf import settings

from .base import NotificationProvider
from .console import ConsoleProvider
from .telegram import TelegramProvider, from_settings as telegram_from_settings


@lru_cache(maxsize=4)
def _cached(name: str, signature: tuple) -> NotificationProvider:
    """Inner cache keyed by (name, settings tuple).

    ``signature`` is passed by the public `get_provider` so a settings
    swap (e.g. tests override `TELEGRAM_BOT_TOKEN`) makes a new instance.
    """
    if name == "console":
        return ConsoleProvider()
    if name == "telegram":
        return telegram_from_settings()
    raise RuntimeError(
        f"NOTIFICATION_PROVIDER={name!r} is not recognized. "
        f"Known: 'console', 'telegram'."
    )


def get_provider() -> NotificationProvider:
    name = settings.NOTIFICATION_PROVIDER
    sig = (
        getattr(settings, "TELEGRAM_BOT_TOKEN", ""),
        getattr(settings, "TELEGRAM_API_BASE", ""),
        getattr(settings, "NOTIFICATION_TIMEOUT_SECONDS", 5),
    )
    return _cached(name, sig)


def reset_cache():
    """Tests call this between cases to ensure clean provider instances."""
    _cached.cache_clear()
