"""
Admin-only owner dashboard (Phase 3).

A1 — Today's Report at `/dashboard/`. The screen is shared across:
- A salesman selector (default *All salesmen*; pick one to see exactly
  what that salesman sees).
- A date picker (default today; admin can audit any past day).
- Live transaction feed, top Baaki list, per-salesman cards.

Every aggregation goes through the per-salesman scoping helpers from
PLAN §1 / §3 so global vs scoped numbers come from the same source of
truth as the salesman flow.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Max, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import jio_import
from .models import Payment, Retailer, Sale, Visit
from .permissions import admin_required


User = get_user_model()


def _parse_date(value):
    if not value:
        return timezone.localdate()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return timezone.localdate()


def _parse_salesman(value):
    """Resolve the `?salesman=` query param to a User instance or None.

    None means *All salesmen*. An unknown id is treated as None.
    """
    if not value or value == "all":
        return None
    try:
        return User.objects.filter(role=User.Role.SALESMAN, pk=int(value)).first()
    except (ValueError, TypeError):
        return None


def _day_range(date):
    tz = timezone.get_current_timezone()
    start = datetime.combine(date, datetime.min.time()).replace(tzinfo=tz)
    return start, start + timedelta(days=1)


def _scope_filter(qs, salesman):
    return qs.filter(salesman=salesman) if salesman else qs


@admin_required
def today(request):
    date = _parse_date(request.GET.get("date"))
    salesman = _parse_salesman(request.GET.get("salesman"))
    day_start, day_end = _day_range(date)

    base_sales = Sale.objects.filter(
        is_deleted=False, occurred_at__gte=day_start, occurred_at__lt=day_end
    )
    base_payments = Payment.objects.filter(
        is_deleted=False, occurred_at__gte=day_start, occurred_at__lt=day_end
    )
    today_sales = _scope_filter(base_sales, salesman)
    today_payments = _scope_filter(base_payments, salesman)

    udhar_total = today_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    cash_total = today_payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    upi_total = today_payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    jama_total = cash_total + upi_total

    # Per-salesman cards: only when *All salesmen* is selected.
    per_salesman = None
    if salesman is None:
        per_salesman = []
        for sm in User.objects.filter(role=User.Role.SALESMAN, is_active=True).order_by("full_name", "username"):
            sm_sales = base_sales.filter(salesman=sm)
            sm_payments = base_payments.filter(salesman=sm)
            per_salesman.append({
                "salesman": sm,
                "udhar": sm_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "cash": sm_payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "upi": sm_payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "entries": sm_sales.count() + sm_payments.count(),
                "visits": Visit.objects.filter(
                    salesman=sm,
                    last_activity_at__gte=day_start,
                    last_activity_at__lt=day_end,
                ).count(),
            })

    today_sales_list = list(
        today_sales.select_related("retailer", "salesman").order_by("-occurred_at")
    )

    # Top Baaki — scope follows the salesman selector. Live, not date-filtered.
    scoped_retailer_qs = (
        Retailer.objects.with_baaki(salesman=salesman)
        .filter(baaki__gt=0)
        .order_by("-baaki")
    )
    top_baaki = list(scoped_retailer_qs[:10])
    n_dukaan_with_baaki = scoped_retailer_qs.count()
    total_baaki = scoped_retailer_qs.aggregate(s=Sum("baaki"))["s"] or Decimal("0")

    # Live transaction feed: 50 most recent entries across all of (or one) salesmen.
    recent_sales_qs = _scope_filter(Sale.objects.filter(is_deleted=False), salesman)
    recent_payments_qs = _scope_filter(Payment.objects.filter(is_deleted=False), salesman)
    recent_sales = list(recent_sales_qs.select_related("retailer", "salesman").order_by("-occurred_at")[:50])
    recent_payments = list(recent_payments_qs.select_related("retailer", "salesman").order_by("-occurred_at")[:50])
    feed = sorted(
        [("sale", s) for s in recent_sales] + [("payment", p) for p in recent_payments],
        key=lambda pair: pair[1].occurred_at,
        reverse=True,
    )[:50]

    all_salesmen = (
        User.objects.filter(role=User.Role.SALESMAN, is_active=True)
        .order_by("full_name", "username")
    )

    visits_today = _scope_filter(
        Visit.objects.filter(last_activity_at__gte=day_start, last_activity_at__lt=day_end),
        salesman,
    ).count()

    ctx = {
        "active": "today",
        "date": date,
        "date_iso": date.isoformat(),
        "selected_salesman": salesman,
        "all_salesmen": all_salesmen,
        "udhar_total": udhar_total,
        "udhar_count": today_sales.count(),
        "cash_total": cash_total,
        "upi_total": upi_total,
        "jama_total": jama_total,
        "per_salesman": per_salesman,
        "today_sales": today_sales_list,
        "top_baaki": top_baaki,
        "total_baaki": total_baaki,
        "n_dukaan_with_baaki": n_dukaan_with_baaki,
        "feed": feed,
        "visits_today": visits_today,
        "is_today": date == timezone.localdate(),
    }

    template = (
        "dashboard/_today_main.html" if request.htmx else "dashboard/today.html"
    )
    return render(request, template, ctx)


# ---------------------------------------------------------------------------
# A2 — Retailers list
# ---------------------------------------------------------------------------


@admin_required
def retailers(request):
    """A2 — searchable, sortable retailer list with scope-aware Baaki and
    last-activity metadata. Salesman selector follows PLAN §5 Phase 3.
    """
    salesman = _parse_salesman(request.GET.get("salesman"))
    search = (request.GET.get("q") or "").strip()
    sort = request.GET.get("sort", "baaki_desc")

    qs = Retailer.objects.filter(is_active=True).with_baaki(salesman=salesman)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(area__icontains=search))

    if sort == "name":
        qs = qs.order_by("name")
    elif sort == "baaki_asc":
        qs = qs.order_by("baaki", "name")
    elif sort == "recent":
        qs = qs.order_by("-updated_at")
    else:
        sort = "baaki_desc"
        qs = qs.order_by("-baaki", "name")

    retailers_list = list(qs)

    # Per-retailer last-activity. For now, an N+1 against sales + payments —
    # acceptable while the retailer count stays small (V1 has dozens). When it
    # crosses ~500 rewrite as two Subquery+Max annotations + Coalesce.
    now = timezone.now()
    for r in retailers_list:
        sales_qs = r.sales.filter(is_deleted=False)
        payments_qs = r.payments.filter(is_deleted=False)
        if salesman:
            sales_qs = sales_qs.filter(salesman=salesman)
            payments_qs = payments_qs.filter(salesman=salesman)
        last_sale = sales_qs.aggregate(d=Max("occurred_at"))["d"]
        last_payment = payments_qs.aggregate(d=Max("occurred_at"))["d"]
        candidates = [d for d in (last_sale, last_payment) if d]
        r.last_entry_at = max(candidates) if candidates else None
        r.days_since = (now - r.last_entry_at).days if r.last_entry_at else None

    all_salesmen = (
        User.objects.filter(role=User.Role.SALESMAN, is_active=True)
        .order_by("full_name", "username")
    )

    ctx = {
        "active": "retailers",
        "retailers": retailers_list,
        "search": search,
        "sort": sort,
        "selected_salesman": salesman,
        "all_salesmen": all_salesmen,
    }
    template = (
        "dashboard/_retailers_results.html"
        if request.htmx
        else "dashboard/retailers.html"
    )
    return render(request, template, ctx)


# ---------------------------------------------------------------------------
# A4 — Salesmen list + per-salesman drill-down
# ---------------------------------------------------------------------------


def _salesman_period_stats(user, since):
    """Aggregate Udhar/Jama for one salesman from `since` to now."""
    sales = Sale.objects.filter(salesman=user, is_deleted=False, occurred_at__gte=since)
    payments = Payment.objects.filter(salesman=user, is_deleted=False, occurred_at__gte=since)
    return {
        "udhar": sales.aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "cash": payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "upi": payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "entries": sales.count() + payments.count(),
    }


def _scoped_total_baaki(user):
    """Sum of this salesman's outstanding Baaki across all retailers."""
    qs = Retailer.objects.with_baaki(salesman=user)
    return qs.aggregate(s=Sum("baaki"))["s"] or Decimal("0")


@admin_required
def salesmen(request):
    """A4 — list of salesmen with this-week stats and outstanding Baaki."""
    now = timezone.now()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    rows = []
    for sm in User.objects.filter(role=User.Role.SALESMAN).order_by("-is_active", "full_name", "username"):
        week_stats = _salesman_period_stats(sm, week_start)
        month_stats = _salesman_period_stats(sm, month_start)
        last_sale = (
            Sale.objects.filter(salesman=sm, is_deleted=False)
            .order_by("-occurred_at").values("occurred_at").first()
        )
        last_payment = (
            Payment.objects.filter(salesman=sm, is_deleted=False)
            .order_by("-occurred_at").values("occurred_at").first()
        )
        candidates = [d["occurred_at"] for d in (last_sale, last_payment) if d]
        last_active = max(candidates) if candidates else None
        rows.append({
            "salesman": sm,
            "week": week_stats,
            "month": month_stats,
            "baaki": _scoped_total_baaki(sm),
            "last_active": last_active,
            "days_since": (now - last_active).days if last_active else None,
        })

    return render(request, "dashboard/salesmen.html", {
        "active": "salesmen",
        "rows": rows,
        "week_start": week_start,
        "month_start": month_start,
    })


@admin_required
def salesman_detail(request, pk):
    """Per-salesman drill-down: stats + their full activity timeline."""
    sm = get_object_or_404(User, pk=pk, role=User.Role.SALESMAN)
    now = timezone.now()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    week_stats = _salesman_period_stats(sm, week_start)
    month_stats = _salesman_period_stats(sm, month_start)
    total_baaki = _scoped_total_baaki(sm)
    n_dukaan = Retailer.objects.with_baaki(salesman=sm).filter(baaki__gt=0).count()

    sales = list(
        Sale.objects.filter(salesman=sm, is_deleted=False)
        .select_related("retailer").order_by("-occurred_at")[:100]
    )
    payments = list(
        Payment.objects.filter(salesman=sm, is_deleted=False)
        .select_related("retailer").order_by("-occurred_at")[:100]
    )
    timeline = sorted(
        [("sale", s) for s in sales] + [("payment", p) for p in payments],
        key=lambda pair: pair[1].occurred_at,
        reverse=True,
    )[:100]

    # Top retailers where this salesman has Baaki outstanding.
    top_baaki_retailers = list(
        Retailer.objects.with_baaki(salesman=sm).filter(baaki__gt=0).order_by("-baaki")[:10]
    )

    return render(request, "dashboard/salesman_detail.html", {
        "active": "salesmen",
        "sm": sm,
        "week": week_stats,
        "month": month_stats,
        "total_baaki": total_baaki,
        "n_dukaan": n_dukaan,
        "timeline": timeline,
        "top_baaki_retailers": top_baaki_retailers,
    })


# ---------------------------------------------------------------------------
# A3 — Retailer detail (admin's full ledger view, scope-aware)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jio import (Phase B of the jio-import branch)
# ---------------------------------------------------------------------------


@admin_required
def jio_import_view(request):
    """Two-step admin flow for ingesting a Jio auto-refill report.

    GET (no session state)      → show the upload form.
    POST with `file=`           → parse + plan + render the preview.
    POST with `confirm=1`       → re-parse the stashed content + apply.
    """
    if request.method == "POST" and request.POST.get("confirm"):
        stashed = request.session.get("jio_import_content")
        if not stashed:
            return render(
                request, "dashboard/jio_import/upload.html",
                {"active": "import", "error": "Session expired. Upload the file again."},
            )
        content = jio_import.unstash_file_content(stashed)
        raw_rows = jio_import.parse_file_content(content)
        rows, parse_errors = jio_import.validate_rows(raw_rows)
        plan = jio_import.plan_import(rows)
        plan.parse_errors = parse_errors
        result = jio_import.apply_plan(plan, request.user)
        request.session.pop("jio_import_content", None)
        return render(
            request, "dashboard/jio_import/result.html",
            {"active": "import", "result": result, "filename": request.session.pop("jio_import_filename", None)},
        )

    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            return render(
                request, "dashboard/jio_import/upload.html",
                {"active": "import", "error": "Please pick a file to upload."},
            )
        content = f.read()
        try:
            raw_rows = jio_import.parse_file_content(content)
        except Exception as e:  # pragma: no cover — parser errors surface here
            return render(
                request, "dashboard/jio_import/upload.html",
                {"active": "import", "error": f"Could not parse file: {e}"},
            )
        if not raw_rows:
            return render(
                request, "dashboard/jio_import/upload.html",
                {"active": "import", "error": "File contained no rows."},
            )
        rows, parse_errors = jio_import.validate_rows(raw_rows)
        plan = jio_import.plan_import(rows)
        plan.parse_errors = parse_errors
        request.session["jio_import_content"] = jio_import.stash_file_content(content)
        request.session["jio_import_filename"] = f.name
        return render(
            request, "dashboard/jio_import/preview.html",
            {"active": "import", "plan": plan, "filename": f.name},
        )

    return render(request, "dashboard/jio_import/upload.html", {"active": "import"})


@admin_required
def retailer_detail(request, pk):
    """A3 — full retailer ledger with salesman selector.

    Admins see every entry by default; picking one salesman scopes the
    timeline + Baaki to that salesman's contribution (matches what they'd
    see in the salesman view). Edit / delete on individual entries opens
    the corresponding Django Admin page — no inline editor yet.
    """
    retailer = get_object_or_404(Retailer, pk=pk)
    salesman = _parse_salesman(request.GET.get("salesman"))

    sales_qs = retailer.sales.select_related("salesman").order_by("-occurred_at")
    payments_qs = retailer.payments.select_related("salesman").order_by("-occurred_at")
    if salesman:
        sales_qs = sales_qs.filter(salesman=salesman)
        payments_qs = payments_qs.filter(salesman=salesman)

    timeline = sorted(
        [("sale", s) for s in sales_qs] + [("payment", p) for p in payments_qs],
        key=lambda pair: pair[1].occurred_at,
        reverse=True,
    )

    all_salesmen = (
        User.objects.filter(role=User.Role.SALESMAN, is_active=True)
        .order_by("full_name", "username")
    )

    ctx = {
        "active": "retailers",
        "retailer": retailer,
        "baaki": retailer.baaki_for(salesman),
        "timeline": timeline,
        "selected_salesman": salesman,
        "all_salesmen": all_salesmen,
    }
    template = (
        "dashboard/_retailer_detail_main.html"
        if request.htmx
        else "dashboard/retailer_detail.html"
    )
    return render(request, template, ctx)
