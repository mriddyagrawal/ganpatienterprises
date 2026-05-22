"""
Role-based access guards.

- `@salesman_required` (Phase 2): salesman-only views. Admins are bounced
  to the owner dashboard at `/dashboard/`; non-role users get a 403.
- `@admin_required` (Phase 3): admin-only views (custom dashboard).
  Salesmen are bounced back to their app at `/`; non-role users get a 403.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect


def salesman_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        if getattr(user, "is_admin_role", False):
            return redirect("/dashboard/")
        if not getattr(user, "is_salesman_role", False):
            return HttpResponseForbidden("Salesman access only.")
        return view_func(request, *args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        if getattr(user, "is_salesman_role", False):
            return redirect("/")
        if not getattr(user, "is_admin_role", False):
            return HttpResponseForbidden("Admin access only.")
        return view_func(request, *args, **kwargs)

    return wrapped
