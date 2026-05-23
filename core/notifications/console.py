"""Dev/test provider: logs the body instead of hitting an upstream.

Used in:
- local dev when ``NOTIFICATION_PROVIDER=console`` (default in base.py)
- the test suite, so unit tests don't need a Telegram token

Always returns SENT. Tests that need to exercise the retry chain use the
:class:`FailingConsoleProvider` variant.
"""
from __future__ import annotations

import logging

from .base import NotificationProvider, SendOutcome, SendResult

logger = logging.getLogger("core.notifications.console")


class ConsoleProvider(NotificationProvider):
    # Honest channel value — `console` is a real Notification.Channel
    # enum entry. Means a future operator-driven swap from console →
    # telegram won't try to use phones-as-chat-ids on queued rows.
    channel = "console"

    def address_for(self, retailer):
        # In dev, prefer telegram_chat_id (mirrors the Telegram provider);
        # fall back to phone so blank-chat-id retailers still get a body
        # logged for QA. Tests that need "no address" pass blank for
        # both fields.
        return retailer.telegram_chat_id or retailer.phone

    def send(self, *, address: str, body: str) -> SendResult:
        logger.info("[console-notify] to=%s\n%s", address, body)
        return SendResult(outcome=SendOutcome.SENT, provider_message_id="console")
