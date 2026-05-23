"""Outbound notifications — Telegram first, SMS/WhatsApp later.

Public API:
- :class:`NotificationProvider` — the ABC every concrete provider implements
- :class:`SendResult` — what ``send()`` returns
- :func:`get_provider` — settings-driven factory; raises if mis-configured
- :func:`build_body` — render the body for a Notification kind
"""
from .base import NotificationProvider, SendResult, SendOutcome
from .factory import get_provider
from .messages import build_body

__all__ = [
    "NotificationProvider",
    "SendResult",
    "SendOutcome",
    "get_provider",
    "build_body",
]
