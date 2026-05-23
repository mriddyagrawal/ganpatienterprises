"""
Lightweight audit logging.

The AuditLog model is the long-term store. This module wraps it so callers
don't have to know the snapshot shape. Phase 1 callers are the Django Admin
classes (`save_model` / `delete_model`). Phase 2 will add salesman-facing
views that call `log_change` directly.
"""

from typing import Any

from django.db import models as djmodels

from .models import AuditLog


_JSON_PRIMITIVES = (type(None), bool, int, float, str)


def _coerce(value: Any) -> Any:
    if isinstance(value, _JSON_PRIMITIVES):
        return value
    return str(value)


def snapshot(instance) -> dict[str, Any]:
    """Capture a JSON-serializable snapshot of all concrete fields on `instance`.

    For ForeignKey fields the snapshot stores both the underlying id (e.g.,
    `retailer_id: 7`) and the human-readable repr (e.g., `retailer: "Mobile
    Shoppy"`). The id preserves a stable reference to the related row; the
    repr makes the log readable when someone is reading it months later. If
    only the repr were stored, renames or edits on the related row would
    silently destroy the link back — the audit log would no longer be
    forensic.
    """
    out: dict[str, Any] = {}
    for f in instance._meta.fields:
        if isinstance(f, djmodels.ForeignKey):
            out[f.attname] = getattr(instance, f.attname)
            out[f.name] = _coerce(getattr(instance, f.name))
        else:
            out[f.name] = _coerce(getattr(instance, f.name))
    return out


def log_change(
    *,
    actor,
    instance,
    action: str,
    before: dict | None = None,
    reason: str = "",
) -> AuditLog:
    """Record an AuditLog row for `instance`.

    - `actor` may be None (system / unauthenticated context).
    - `action` should be one of AuditLog.Action.* values.
    - `before` is the pre-change snapshot for updates/deletes.
    - `reason` is the WHY supplied by the operator on edits/deletes.
      Salesman-facing forms require it; the importer / admin-bulk path
      passes "" because they don't have a user-supplied reason.
    """
    return AuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        entity_type=instance.__class__.__name__,
        entity_id=instance.pk,
        action=action,
        before=before,
        after=snapshot(instance) if action != AuditLog.Action.DELETE else None,
        reason=reason,
    )
