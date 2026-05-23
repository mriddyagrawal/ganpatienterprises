"""
Salesman-facing views (Phase 2).

Every view is scoped to ``request.user`` per the per-salesman data rule
(PLAN §1, §3, project-data-scoping memory). Admins are bounced to the
Django Admin and never see the salesman shell.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_change, snapshot
from .forms import DeleteEntryForm, PaymentForm
from .models import AuditLog, Payment, Retailer, Sale, Visit
from .notifications import (
    enqueue_payment_cancelled,
    enqueue_payment_received,
    notify_on_edit_if_needed,
)
from .permissions import salesman_required


# How long a salesman can edit / delete his own entry (PLAN §3).
SALESMAN_EDIT_WINDOW = timedelta(hours=24)

# Soft warning threshold for single Sale / Payment amounts (PLAN §3).
SANITY_WARN_AMOUNT = Decimal("100000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_salesman_edit(entry, user) -> bool:
    """True if `user` is the entry's owner and the 24h window is open."""
    if entry.salesman_id != user.id:
        return False
    return (timezone.now() - entry.created_at) <= SALESMAN_EDIT_WINDOW


def _attach_editable_flag(entries, user):
    for e in entries:
        e.is_editable = _can_salesman_edit(e, user)


# ---------------------------------------------------------------------------
# Root / Dukaan tab
# ---------------------------------------------------------------------------


@salesman_required
def dukaan(request):
    """Salesman home (Dukaan tab).

    Filtered to retailers where `assigned_salesman = request.user`.
    The assignment is one-to-one (each retailer has at most one
    responsible salesman), set on retailer auto-create from a Jio
    import and otherwise edited via Django Admin. Salesmen never see
    other people's retailers in this list.

    Live HTMX search: when ``request.htmx`` is true we return just the
    results partial so the page doesn't reload on each keystroke.
    """
    user = request.user
    qs = (
        Retailer.objects.filter(is_active=True, assigned_salesman=user)
        .with_baaki(salesman=user)
    )

    search = (request.GET.get("q") or "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(area__icontains=search))

    sort = request.GET.get("sort", "baaki")
    if sort == "name":
        qs = qs.order_by("name")
    elif sort == "recent":
        qs = qs.order_by("-updated_at")
    else:
        sort = "baaki"
        qs = qs.order_by("-baaki", "name")

    template = (
        "salesman/_dukaan_results.html" if request.htmx else "salesman/dukaan_list.html"
    )
    return render(
        request,
        template,
        {"active": "dukaan", "retailers": qs, "search": search, "sort": sort},
    )


# ---------------------------------------------------------------------------
# Retailer detail (the ledger)
# ---------------------------------------------------------------------------


@salesman_required
def retailer_detail(request, pk):
    user = request.user
    # Strict mode (Phase C followup): salesmen can only open retailers
    # assigned to them. Cross-coverage is captured as a future-plan; see
    # `futureplans.md` #10. The 404 (not 403) is intentional — to a
    # non-assigned salesman this retailer effectively doesn't exist.
    retailer = get_object_or_404(
        Retailer, pk=pk, is_active=True, assigned_salesman=user
    )

    sales = list(retailer.sales.filter(salesman=user).order_by("-occurred_at"))
    payments = list(retailer.payments.filter(salesman=user).order_by("-occurred_at"))
    _attach_editable_flag(sales + payments, user)

    timeline = sorted(
        [("sale", s) for s in sales] + [("payment", p) for p in payments],
        key=lambda pair: pair[1].occurred_at,
        reverse=True,
    )

    return render(
        request,
        "salesman/retailer_detail.html",
        {
            "active": None,
            "retailer": retailer,
            "baaki": retailer.baaki_for(user),
            "timeline": timeline,
        },
    )


# ---------------------------------------------------------------------------
# Naya Entry — retailer picker + form
# ---------------------------------------------------------------------------


@salesman_required
def entry_new_picker(request):
    """First step of Naya Entry when no retailer is in context yet.

    Filtered to retailers assigned to this salesman. Cross-coverage at
    someone else's retailer isn't supported in V1 — see futureplans.md
    #10 for the workflow if/when the business requires it.
    """
    user = request.user
    qs = (
        Retailer.objects.filter(is_active=True, assigned_salesman=user)
        .with_baaki(salesman=user)
        .order_by("name")
    )
    search = (request.GET.get("q") or "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(area__icontains=search))
    template = (
        "salesman/_entry_picker_results.html"
        if request.htmx
        else "salesman/entry_picker.html"
    )
    return render(
        request,
        template,
        {"active": "naya", "retailers": qs, "search": search},
    )


@salesman_required
def entry_new(request, pk):
    """Show / process the Jama entry form for a specific retailer.

    Salesmen only enter Jama (cash / UPI received). All Udhar comes in
    via the Jio auto-refill import (see /dashboard/import/). Admins can
    still create Sales manually via Django Admin if needed for the rare
    non-Jio case.

    Strict mode (Phase C followup): the retailer must be assigned to
    the logged-in salesman. Cross-coverage is in `futureplans.md` #10.
    """
    user = request.user
    retailer = get_object_or_404(
        Retailer, pk=pk, is_active=True, assigned_salesman=user
    )

    if request.method == "POST":
        payment_form = PaymentForm(request.POST)
        if payment_form.is_valid():
            payment = payment_form.save(commit=False)
            payment.retailer = retailer
            payment.salesman = user
            payment.save()
            log_change(actor=user, instance=payment, action=AuditLog.Action.CREATE)
            enqueue_payment_received(payment)
            return redirect("core:retailer_detail", pk=retailer.pk)
    else:
        payment_form = PaymentForm()

    return render(
        request,
        "salesman/entry_form.html",
        {
            "active": "naya",
            "retailer": retailer,
            "baaki": retailer.baaki_for(user),
            "payment_form": payment_form,
            "sanity_warn_amount": SANITY_WARN_AMOUNT,
            "is_edit": False,
        },
    )


# ---------------------------------------------------------------------------
# Edit / soft-delete
# ---------------------------------------------------------------------------


@salesman_required
def entry_edit(request, kind, pk):
    """Salesmen can only edit their own Payments (Jama). Imported Sales
    (Udhar) are read-only on the salesman side — admin edits them via
    Django Admin if a correction is needed."""
    if kind != "jama":
        return HttpResponseForbidden("Salesmen can only edit Jama entries.")

    user = request.user
    entry = get_object_or_404(Payment, pk=pk, salesman=user, is_deleted=False)

    if not _can_salesman_edit(entry, user):
        return HttpResponseForbidden("24-ghante ka edit window khatam ho gaya.")

    # Snapshot the pre-edit state BEFORE PaymentForm.is_valid() runs —
    # ModelForm's _post_clean() mutates `entry` in place when the form
    # binds, so any snapshot taken after is_valid() captures the new
    # values, not the original ones. The audit log + the "amount
    # changed?" decision in notify_on_edit_if_needed both depend on
    # this pre-edit snapshot being accurate.
    form = PaymentForm(request.POST or None, instance=entry)
    if request.method == "POST":
        before = snapshot(entry)
        if form.is_valid():
            form.save()
            log_change(
                actor=user, instance=entry, action=AuditLog.Action.UPDATE, before=before
            )
            notify_on_edit_if_needed(entry, before=before)
            return redirect("core:retailer_detail", pk=entry.retailer_id)

    return render(
        request,
        "salesman/entry_form.html",
        {
            "active": None,
            "retailer": entry.retailer,
            "baaki": entry.retailer.baaki_for(user),
            "payment_form": form,
            "sanity_warn_amount": SANITY_WARN_AMOUNT,
            "is_edit": True,
            "entry": entry,
        },
    )


@salesman_required
def entry_delete(request, kind, pk):
    """Salesmen can only soft-delete their own Payments (Jama)."""
    if kind != "jama":
        return HttpResponseForbidden("Salesmen can only delete Jama entries.")

    user = request.user
    entry = get_object_or_404(Payment, pk=pk, salesman=user, is_deleted=False)

    if not _can_salesman_edit(entry, user):
        return HttpResponseForbidden("24-ghante ka delete window khatam ho gaya.")

    if request.method == "POST":
        form = DeleteEntryForm(request.POST)
        if form.is_valid():
            before = snapshot(entry)
            entry.is_deleted = True
            entry.deleted_reason = form.cleaned_data["reason"]
            entry.save()
            log_change(
                actor=user, instance=entry, action=AuditLog.Action.DELETE, before=before
            )
            enqueue_payment_cancelled(entry)
            return redirect("core:retailer_detail", pk=entry.retailer_id)
    else:
        form = DeleteEntryForm()

    return render(
        request,
        "salesman/entry_delete.html",
        {"active": None, "entry": entry, "kind": kind, "form": form},
    )


# ---------------------------------------------------------------------------
# Aaj tab (today's report — scoped to this salesman)
# ---------------------------------------------------------------------------


@salesman_required
def aaj(request):
    user = request.user
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    today_sales = Sale.objects.filter(
        salesman=user,
        is_deleted=False,
        occurred_at__gte=today_start,
        occurred_at__lt=today_end,
    )
    today_payments = Payment.objects.filter(
        salesman=user,
        is_deleted=False,
        occurred_at__gte=today_start,
        occurred_at__lt=today_end,
    )

    udhar_total = today_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    cash_total = today_payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    upi_total = today_payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    jama_total = cash_total + upi_total

    visits_today = Visit.objects.filter(
        salesman=user,
        last_activity_at__gte=today_start,
        last_activity_at__lt=today_end,
    ).count()

    today_sales_list = list(today_sales.select_related("retailer").order_by("-occurred_at"))
    _attach_editable_flag(today_sales_list, user)

    # Top Baaki dukaan — only retailers assigned to this salesman with outstanding Baaki.
    scoped_qs = (
        Retailer.objects.filter(assigned_salesman=user)
        .with_baaki(salesman=user)
        .filter(baaki__gt=0)
        .order_by("-baaki")
    )
    top_baaki = list(scoped_qs[:10])
    n_dukaan_with_baaki = scoped_qs.count()
    total_baaki = scoped_qs.aggregate(s=Sum("baaki"))["s"] or Decimal("0")

    return render(
        request,
        "salesman/aaj.html",
        {
            "active": "aaj",
            "today": today_start,
            "udhar_total": udhar_total,
            "udhar_count": today_sales.count(),
            "jama_total": jama_total,
            "cash_total": cash_total,
            "upi_total": upi_total,
            "today_sales": today_sales_list,
            "top_baaki": top_baaki,
            "total_baaki": total_baaki,
            "n_dukaan_with_baaki": n_dukaan_with_baaki,
            "visits_today": visits_today,
        },
    )
