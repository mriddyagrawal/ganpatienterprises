"""Enqueue helpers — call from views/forms when a Payment lifecycle event fires.

These write a Notification row with ``status=queued``. The
``dispatch_notifications`` management command picks them up.

Why an in-DB queue rather than synchronous send: the salesman's submit
button shouldn't wait on an external API. Reliability is also better —
a transient Telegram outage doesn't lose the message; the next dispatcher
poll retries via the chain.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from ..models import Notification
from .factory import get_provider
from .messages import build_body


def _enqueue(*, payment, kind: str, previous_amount: Decimal | None = None) -> Notification | None:
    """Internal: build one Notification row for this payment+kind.

    Returns the row, or None when the active provider has no address for
    this retailer (so we skip rather than queue something that can never
    succeed).
    """
    provider = get_provider()
    address = provider.address_for(payment.retailer)
    if not address:
        return None
    body = build_body(kind=kind, payment=payment, previous_amount=previous_amount)
    return Notification.objects.create(
        payment=payment,
        kind=kind,
        channel=provider.channel,
        address=address,
        body=body,
        status=Notification.Status.QUEUED,
        attempt_number=1,
        send_after=timezone.now(),
    )


def enqueue_payment_received(payment) -> Notification | None:
    return _enqueue(payment=payment, kind=Notification.Kind.RECEIVED)


def enqueue_payment_updated(payment, *, previous_amount: Decimal) -> Notification | None:
    """Caller decides whether this edit warrants a notification.

    Convention: enqueue only when amount or mode actually changed — pure
    notes edits shouldn't pester the retailer. See `notify_on_edit_if_needed`.
    """
    return _enqueue(
        payment=payment, kind=Notification.Kind.UPDATED,
        previous_amount=previous_amount,
    )


def enqueue_payment_cancelled(payment) -> Notification | None:
    return _enqueue(payment=payment, kind=Notification.Kind.CANCELLED)


def notify_on_edit_if_needed(payment, *, before: dict) -> Notification | None:
    """Decide whether the edit is material, enqueue if so.

    ``before`` is the snapshot dict from `core.audit.snapshot`. Comparison
    is on amount and mode only — notes/occurred_at/visit changes don't
    matter to the retailer.
    """
    before_amount = Decimal(str(before.get("amount", "0")))
    before_mode = before.get("mode")
    if before_amount == payment.amount and before_mode == payment.mode:
        return None
    return enqueue_payment_updated(payment, previous_amount=before_amount)
