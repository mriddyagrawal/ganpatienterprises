"""Provider ABC and result types.

A provider knows three things:
- which channel enum value it implements (telegram/sms/whatsapp)
- which Retailer field carries its address (telegram_chat_id vs. phone)
- how to actually call its upstream API

That keeps Payment-side code dumb: it asks the factory for the active
provider, asks the provider what address to use for a retailer, and
calls send(). The chain doesn't care which provider is wired.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SendOutcome(str, Enum):
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single provider.send() call.

    ``provider_message_id`` and ``error`` are mutually exclusive — set the
    one that matches ``outcome``. The dispatcher writes both back into the
    matching Notification row.
    """

    outcome: SendOutcome
    provider_message_id: str = ""
    error: str = ""


class NotificationProvider:
    """Abstract base. Concrete providers live next to this file."""

    # Notification.Channel value this provider implements.
    channel: str = ""

    def address_for(self, retailer) -> str:
        """Return the field on the Retailer that this provider sends to.

        Default: ``retailer.phone`` (SMS / WhatsApp). Telegram overrides
        to read ``telegram_chat_id`` instead. Returns empty string when
        the retailer doesn't have an address for this channel — the
        caller skips enqueue rather than enqueue-then-fail.
        """
        return retailer.phone

    def send(self, *, address: str, body: str) -> SendResult:
        raise NotImplementedError
