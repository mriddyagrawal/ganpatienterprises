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
from .forms import DeleteEntryForm, PaymentForm, SaleForm
from .models import AuditLog, Payment, Retailer, Sale, Visit
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


def _entry_model_for_kind(kind: str):
    if kind == "udhar":
        return Sale, SaleForm
    if kind == "jama":
        return Payment, PaymentForm
    return None, None


# ---------------------------------------------------------------------------
# Root / Dukaan tab
# ---------------------------------------------------------------------------


@salesman_required
def dukaan(request):
    """Salesman home (Dukaan tab).

    `@salesman_required` already redirects admins to ``/admin/`` and 403s
    anyone else, so this view only handles the happy path.

    Live HTMX search: when ``request.htmx`` is true we return just the
    results partial so the page doesn't reload on each keystroke
    (PLAN §5 Phase 2 S2 — "live filter, HTMX").
    """
    user = request.user
    qs = Retailer.objects.filter(is_active=True).with_baaki(salesman=user)

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
    retailer = get_object_or_404(Retailer, pk=pk, is_active=True)

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

    Same HTMX-partial pattern as :func:`dukaan` for the live search.
    """
    user = request.user
    qs = (
        Retailer.objects.filter(is_active=True)
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
    """Show / process the new-entry form for a specific retailer."""
    user = request.user
    retailer = get_object_or_404(Retailer, pk=pk, is_active=True)
    kind = (request.POST.get("kind") or request.GET.get("kind") or "").strip()

    sale_form = None
    payment_form = None
    kind_error = None

    if request.method == "POST":
        if kind == "udhar":
            sale_form = SaleForm(request.POST)
            if sale_form.is_valid():
                sale = sale_form.save(commit=False)
                sale.retailer = retailer
                sale.salesman = user
                sale.save()
                log_change(actor=user, instance=sale, action=AuditLog.Action.CREATE)
                return redirect("core:retailer_detail", pk=retailer.pk)
        elif kind == "jama":
            payment_form = PaymentForm(request.POST)
            if payment_form.is_valid():
                payment = payment_form.save(commit=False)
                payment.retailer = retailer
                payment.salesman = user
                payment.save()
                log_change(actor=user, instance=payment, action=AuditLog.Action.CREATE)
                return redirect("core:retailer_detail", pk=retailer.pk)
        else:
            # Defensive: hidden `kind` input was tampered with or the JS toggle
            # state was broken. Don't render a blank form — surface the issue.
            kind_error = "Udhar ya Jama, kuch ek select karein."

    if sale_form is None:
        sale_form = SaleForm()
    if payment_form is None:
        payment_form = PaymentForm()

    return render(
        request,
        "salesman/entry_form.html",
        {
            "active": "naya",
            "retailer": retailer,
            "baaki": retailer.baaki_for(user),
            "kind": kind,
            "kind_error": kind_error,
            "sale_form": sale_form,
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
    Model, Form = _entry_model_for_kind(kind)
    if Model is None:
        return HttpResponseForbidden()

    user = request.user
    entry = get_object_or_404(Model, pk=pk, salesman=user, is_deleted=False)

    if not _can_salesman_edit(entry, user):
        return HttpResponseForbidden("24-ghante ka edit window khatam ho gaya.")

    form = Form(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        before = snapshot(entry)
        form.save()
        log_change(
            actor=user, instance=entry, action=AuditLog.Action.UPDATE, before=before
        )
        return redirect("core:retailer_detail", pk=entry.retailer_id)

    return render(
        request,
        "salesman/entry_form.html",
        {
            "active": None,
            "retailer": entry.retailer,
            "baaki": entry.retailer.baaki_for(user),
            "kind": kind,
            # The form being edited; the other one is just a placeholder for the toggle UI.
            "sale_form": form if kind == "udhar" else SaleForm(),
            "payment_form": form if kind == "jama" else PaymentForm(),
            "sanity_warn_amount": SANITY_WARN_AMOUNT,
            "is_edit": True,
            "entry": entry,
        },
    )


@salesman_required
def entry_delete(request, kind, pk):
    Model, _ = _entry_model_for_kind(kind)
    if Model is None:
        return HttpResponseForbidden()

    user = request.user
    entry = get_object_or_404(Model, pk=pk, salesman=user, is_deleted=False)

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

    # Top Baaki dukaan — this salesman's outstanding only
    scoped_qs = Retailer.objects.with_baaki(salesman=user).filter(baaki__gt=0).order_by("-baaki")
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
