"""
Role-based access guards for salesman-facing views.

Phase 2 wires every salesman view through `@salesman_required`, which:
1. Requires authentication (delegates to login_required).
2. Sends admins to the Django Admin instead — they have their own UI.
3. Rejects anyone else with a 403.
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
            return redirect("/admin/")
        if not getattr(user, "is_salesman_role", False):
            return HttpResponseForbidden("Salesman access only.")
        return view_func(request, *args, **kwargs)

    return wrapped
