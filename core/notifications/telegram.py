"""Telegram bot provider.

Single endpoint, single dependency: stdlib ``urllib`` (no python-telegram-bot
for one API call). The bot must be added to each retailer's chat (1:1 or
group); the retailer-side onboarding is out of scope for this slice.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .base import NotificationProvider, SendOutcome, SendResult

logger = logging.getLogger("core.notifications.telegram")


class TelegramProvider(NotificationProvider):
    channel = "telegram"

    def __init__(self, *, token: str, api_base: str, timeout: float):
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set — cannot use the Telegram "
                "provider. Set it in the .env or switch "
                "NOTIFICATION_PROVIDER to 'console'."
            )
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def address_for(self, retailer) -> str:
        return retailer.telegram_chat_id or ""

    def send(self, *, address: str, body: str) -> SendResult:
        url = f"{self.api_base}/bot{self.token}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": address,
            "text": body,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body_bytes = resp.read()
            data = json.loads(body_bytes.decode("utf-8"))
            if not data.get("ok"):
                return SendResult(
                    outcome=SendOutcome.FAILED,
                    error=f"telegram returned ok=False: {body_bytes!r}",
                )
            message_id = str(data.get("result", {}).get("message_id", ""))
            return SendResult(
                outcome=SendOutcome.SENT, provider_message_id=message_id,
            )
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            return SendResult(
                outcome=SendOutcome.FAILED,
                error=f"HTTP {e.code}: {err_body}",
            )
        except urllib.error.URLError as e:
            return SendResult(
                outcome=SendOutcome.FAILED,
                error=f"network: {e.reason}",
            )
        except Exception as e:  # last-resort; provider must never raise
            logger.exception("telegram send crashed")
            return SendResult(outcome=SendOutcome.FAILED, error=repr(e))


def from_settings() -> TelegramProvider:
    return TelegramProvider(
        token=settings.TELEGRAM_BOT_TOKEN,
        api_base=settings.TELEGRAM_API_BASE,
        timeout=float(settings.NOTIFICATION_TIMEOUT_SECONDS),
    )
