"""
Reports and exports (Phase 4).

Every report honors PLAN §1 data scoping: admin sees everything by
default; a salesman selector scopes to one person's slice. Salesman-
facing report endpoints (when added) will hard-scope to ``request.user``.

The reports here are computed on demand. At V1's scale (handful of
salesmen, a few hundred retailers, low-thousands of entries) this is
cheap and avoids the complexity of pre-aggregated tables.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .dashboard import _parse_date, _parse_salesman, _day_range, _scope_filter
from .models import Payment, Retailer, Sale
from .permissions import admin_required


User = get_user_model()


# ---------------------------------------------------------------------------
# Reports index
# ---------------------------------------------------------------------------


@admin_required
def index(request):
    return render(request, "dashboard/reports/index.html", {"active": "reports"})


# ---------------------------------------------------------------------------
# Daily closing
# ---------------------------------------------------------------------------


@admin_required
def daily_closing(request):
    """For a given date: Σ Udhar, Σ Jama by mode, per-salesman breakdown,
    list of entries. Scope-aware via salesman selector.
    """
    date = _parse_date(request.GET.get("date"))
    salesman = _parse_salesman(request.GET.get("salesman"))
    day_start, day_end = _day_range(date)

    sales_qs = _scope_filter(
        Sale.objects.filter(is_deleted=False, occurred_at__gte=day_start, occurred_at__lt=day_end),
        salesman,
    ).select_related("retailer", "salesman").order_by("-occurred_at")
    payments_qs = _scope_filter(
        Payment.objects.filter(is_deleted=False, occurred_at__gte=day_start, occurred_at__lt=day_end),
        salesman,
    ).select_related("retailer", "salesman").order_by("-occurred_at")

    udhar_total = sales_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    cash_total = payments_qs.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    upi_total = payments_qs.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    jama_total = cash_total + upi_total
    net_position = udhar_total - jama_total  # +ve = Baaki grew; -ve = Baaki shrunk

    # Per-salesman breakdown (only meaningful on All salesmen)
    per_salesman = None
    if salesman is None:
        per_salesman = []
        for sm in User.objects.filter(role=User.Role.SALESMAN).order_by("full_name", "username"):
            sm_sales = sales_qs.filter(salesman=sm)
            sm_payments = payments_qs.filter(salesman=sm)
            per_salesman.append({
                "salesman": sm,
                "udhar": sm_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "cash": sm_payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "upi": sm_payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
                "entries": sm_sales.count() + sm_payments.count(),
            })

    entries = sorted(
        [("sale", s) for s in sales_qs] + [("payment", p) for p in payments_qs],
        key=lambda pair: pair[1].occurred_at,
        reverse=True,
    )

    all_salesmen = User.objects.filter(role=User.Role.SALESMAN, is_active=True).order_by("full_name", "username")

    ctx = {
        "active": "reports",
        "report_name": "Daily Closing",
        "date": date,
        "date_iso": date.isoformat(),
        "selected_salesman": salesman,
        "all_salesmen": all_salesmen,
        "udhar_total": udhar_total,
        "cash_total": cash_total,
        "upi_total": upi_total,
        "jama_total": jama_total,
        "net_position": net_position,
        "per_salesman": per_salesman,
        "entries": entries,
        "n_entries": len(entries),
    }
    template = (
        "dashboard/reports/_daily_closing_main.html"
        if request.htmx
        else "dashboard/reports/daily_closing.html"
    )
    return render(request, template, ctx)


# ---------------------------------------------------------------------------
# Baaki aging (the flagship report)
# ---------------------------------------------------------------------------


AGING_BUCKETS = [
    ("0-7", 0, 7),
    ("8-15", 8, 15),
    ("16-30", 16, 30),
    ("31-60", 31, 60),
    ("60+", 61, None),
]


def _oldest_unsettled_sale(retailer, salesman, as_of) -> Optional[dict]:
    """FIFO-match payments against sales (oldest first) and return the
    oldest sale that still has a non-zero remaining balance.

    Returns dict {age_days, occurred_at, remaining} or None if nothing
    is unsettled (Baaki <= 0).
    """
    sales = list(
        retailer.sales.filter(is_deleted=False)
        .filter(**({"salesman": salesman} if salesman else {}))
        .order_by("occurred_at")
    )
    payments = list(
        retailer.payments.filter(is_deleted=False)
        .filter(**({"salesman": salesman} if salesman else {}))
        .order_by("occurred_at")
    )
    if not sales:
        return None

    # Merge chronologically.
    events = (
        [{"date": s.occurred_at, "kind": "sale", "amount": s.amount} for s in sales]
        + [{"date": p.occurred_at, "kind": "payment", "amount": p.amount} for p in payments]
    )
    events.sort(key=lambda e: e["date"])

    # FIFO queue of [date, remaining_amount].
    queue = []
    for ev in events:
        if ev["kind"] == "sale":
            queue.append([ev["date"], ev["amount"]])
        else:
            remaining = ev["amount"]
            while queue and remaining > 0:
                if queue[0][1] <= remaining:
                    remaining -= queue[0][1]
                    queue.pop(0)
                else:
                    queue[0][1] -= remaining
                    remaining = Decimal("0")
            # Any leftover remaining is overpayment; ignored for aging.

    if not queue:
        return None

    oldest_date, oldest_remaining = queue[0]
    return {
        "occurred_at": oldest_date,
        "age_days": (as_of - oldest_date).days,
        "remaining": oldest_remaining,
    }


@admin_required
def baaki_aging(request):
    """Per-retailer aging report — bucket by oldest unsettled sale's age.

    Scope follows the salesman selector. FIFO matching ensures the "age"
    reflects the actual oldest unpaid debt rather than the date of any
    sale period.
    """
    salesman = _parse_salesman(request.GET.get("salesman"))
    now = timezone.now()

    retailers = (
        Retailer.objects.with_baaki(salesman=salesman)
        .filter(baaki__gt=0)
        .order_by("name")
    )

    bucket_rows = {label: [] for label, _, _ in AGING_BUCKETS}
    bucket_totals = {label: Decimal("0") for label, _, _ in AGING_BUCKETS}
    grand_total = Decimal("0")

    for r in retailers:
        oldest = _oldest_unsettled_sale(r, salesman, now)
        if oldest is None:
            # Defensive: with_baaki said >0 but FIFO finds nothing. Means
            # negative Baaki accumulated then re-incurred — treat as 0-7.
            label = "0-7"
            age_days = 0
        else:
            age_days = oldest["age_days"]
            label = next(
                (lo for lo, lo_min, lo_max in AGING_BUCKETS
                 if age_days >= lo_min and (lo_max is None or age_days <= lo_max)),
                "60+",
            )
        bucket_rows[label].append({
            "retailer": r,
            "baaki": r.baaki,
            "age_days": age_days,
        })
        bucket_totals[label] += r.baaki
        grand_total += r.baaki

    buckets_ordered = [
        {
            "label": label,
            "rows": bucket_rows[label],
            "total": bucket_totals[label],
            "count": len(bucket_rows[label]),
        }
        for label, _, _ in AGING_BUCKETS
    ]

    all_salesmen = User.objects.filter(role=User.Role.SALESMAN, is_active=True).order_by("full_name", "username")

    ctx = {
        "active": "reports",
        "report_name": "Baaki Aging",
        "selected_salesman": salesman,
        "all_salesmen": all_salesmen,
        "buckets": buckets_ordered,
        "grand_total": grand_total,
        "n_retailers": sum(b["count"] for b in buckets_ordered),
    }
    template = (
        "dashboard/reports/_baaki_aging_main.html"
        if request.htmx
        else "dashboard/reports/baaki_aging.html"
    )
    return render(request, template, ctx)
