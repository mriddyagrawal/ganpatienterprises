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
    channel = "telegram"  # for dev — pretend to be Telegram

    def send(self, *, address: str, body: str) -> SendResult:
        logger.info("[console-notify] to=%s\n%s", address, body)
        return SendResult(outcome=SendOutcome.SENT, provider_message_id="console")
