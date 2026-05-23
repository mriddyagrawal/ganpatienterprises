"""Render the message body for each Notification.Kind.

Hinglish, retailer-facing. Money in ₹, payment mode spelled out, the
salesman's name (the retailer's accountability anchor), and a "Edit"
disclaimer that makes back-dated tampering visible to the retailer.
"""
from __future__ import annotations

from decimal import Decimal


def _fmt_amount(amount: Decimal) -> str:
    # No paise — retailers think in whole rupees; matches the rest of the UI.
    return f"₹{int(amount):,}"


def build_body(*, kind: str, payment, previous_amount: Decimal | None = None) -> str:
    """Return the body text for an outbound notification.

    ``kind`` is a value from :class:`core.models.Notification.Kind`.
    ``previous_amount`` is required when ``kind == "updated"`` so the
    retailer sees what changed.
    """
    retailer = payment.retailer
    salesman = payment.salesman
    salesman_name = getattr(salesman, "full_name", None) or salesman.get_username()

    if kind == "received":
        return (
            f"Namaste {retailer.name} ji,\n\n"
            f"{salesman_name} ne aapse {_fmt_amount(payment.amount)} "
            f"({payment.get_mode_display()}) collect kiya hai.\n\n"
            f"— Ganpati Enterprises"
        )

    if kind == "updated":
        if previous_amount is None:
            previous_amount = payment.amount
        return (
            f"Namaste {retailer.name} ji,\n\n"
            f"Aapka payment update hua hai:\n"
            f"Pehle: {_fmt_amount(previous_amount)}\n"
            f"Ab: {_fmt_amount(payment.amount)} ({payment.get_mode_display()})\n\n"
            f"By {salesman_name}\n"
            f"— Ganpati Enterprises"
        )

    if kind == "cancelled":
        return (
            f"Namaste {retailer.name} ji,\n\n"
            f"Aapka {_fmt_amount(payment.amount)} ka payment "
            f"({payment.get_mode_display()}) cancel kar diya gaya hai.\n\n"
            f"By {salesman_name}\n"
            f"— Ganpati Enterprises"
        )

    raise ValueError(f"Unknown notification kind: {kind!r}")
