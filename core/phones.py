"""Indian-mobile phone-number normalization helpers.

PLAN §1 (Locked-in decisions): the business operates entirely within India,
so every phone number we store is normalized to E.164 with the +91 prefix.
Storing in canonical form lets the SMS/WhatsApp/Telegram providers stay
dumb — they read the field, no per-call massaging.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError


def normalize_indian_phone(value: str | None) -> str:
    """Return ``"+91XXXXXXXXXX"`` or ``""`` for blank input.

    Accepts whatever the admin or salesman typed — ``9876543210``,
    ``09876543210``, ``+91 98765 43210``, ``91-98765-43210`` — and returns
    the canonical E.164 form. Raises :class:`ValidationError` if the input
    is non-empty but can't be reduced to a 10-digit Indian mobile.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        raise ValidationError(
            f"Phone has no digits: {value!r}."
        )
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValidationError(
            f"Phone must reduce to a 10-digit Indian mobile; "
            f"got {len(digits)} digit(s) from {value!r}."
        )
    if digits[0] not in "6789":
        raise ValidationError(
            f"Indian mobile numbers start with 6/7/8/9; got {digits[0]!r} "
            f"in {value!r}."
        )
    return f"+91{digits}"
