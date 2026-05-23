"""Outbound notifications — Telegram first, SMS/WhatsApp later.

Public API:
- :class:`NotificationProvider` — the ABC every concrete provider implements
- :class:`SendResult` — what ``send()`` returns
- :func:`get_provider` — settings-driven factory; raises if mis-configured
- :func:`build_body` — render the body for a Notification kind
"""
from .base import NotificationProvider, SendResult, SendOutcome
from .enqueue import (
    enqueue_payment_cancelled,
    enqueue_payment_received,
    enqueue_payment_updated,
    notify_on_edit_if_needed,
)
from .factory import get_provider
from .messages import build_body

__all__ = [
    "NotificationProvider",
    "SendResult",
    "SendOutcome",
    "get_provider",
    "build_body",
    "enqueue_payment_received",
    "enqueue_payment_updated",
    "enqueue_payment_cancelled",
    "notify_on_edit_if_needed",
]
