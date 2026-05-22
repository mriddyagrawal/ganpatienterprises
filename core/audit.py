"""
Lightweight audit logging.

The AuditLog model is the long-term store. This module wraps it so callers
don't have to know the snapshot shape. Phase 1 callers are the Django Admin
classes (`save_model` / `delete_model`). Phase 2 will add salesman-facing
views that call `log_change` directly.
"""

from typing import Any

from .models import AuditLog


_JSON_PRIMITIVES = (type(None), bool, int, float, str)


def _coerce(value: Any) -> Any:
    if isinstance(value, _JSON_PRIMITIVES):
        return value
    return str(value)


def snapshot(instance) -> dict[str, Any]:
    """Capture a JSON-serializable snapshot of all concrete fields on `instance`."""
    return {
        f.name: _coerce(getattr(instance, f.name))
        for f in instance._meta.fields
    }


def log_change(*, actor, instance, action: str, before: dict | None = None) -> AuditLog:
    """Record an AuditLog row for `instance`.

    - `actor` may be None (system / unauthenticated context).
    - `action` should be one of AuditLog.Action.* values.
    - `before` is the pre-change snapshot for updates/deletes.
    """
    return AuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        entity_type=instance.__class__.__name__,
        entity_id=instance.pk,
        action=action,
        before=before,
        after=snapshot(instance) if action != AuditLog.Action.DELETE else None,
    )
