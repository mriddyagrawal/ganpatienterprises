from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


@login_required
def home(request):
    """Role-based home.

    Admins are bounced to the Django Admin. Salesmen see a placeholder
    dashboard for now — the real Phase 2 UI replaces this.
    """
    if getattr(request.user, "is_admin_role", False):
        return redirect("/admin/")
    return render(request, "core/home.html")
