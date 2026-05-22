"""
Reports and exports (Phase 4).

Every report honors PLAN §1 data scoping: admin sees everything by
default; a salesman selector scopes to one person's slice. Salesman-
facing report endpoints (when added) will hard-scope to ``request.user``.

The reports here are computed on demand. At V1's scale (handful of
salesmen, a few hundred retailers, low-thousands of entries) this is
cheap and avoids the complexity of pre-aggregated tables.
"""

import csv
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .dashboard import _parse_date, _parse_salesman, _day_range, _scope_filter
from .models import Payment, Retailer, Sale, Visit
from .permissions import admin_required


# ---------------------------------------------------------------------------
# CSV export helper
# ---------------------------------------------------------------------------


def _csv_response(filename: str, fieldnames: list[str], rows: list[dict]) -> HttpResponse:
    """Stream a list of dicts as a CSV download."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # BOM so Excel opens UTF-8 cleanly.
    response.write("﻿")
    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return response


def _wants_csv(request) -> bool:
    return (request.GET.get("format") or "").lower() == "csv"


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

    if _wants_csv(request):
        rows = []
        for kind, e in entries:
            rows.append({
                "when": e.occurred_at.strftime("%Y-%m-%d %H:%M"),
                "type": "Udhar" if kind == "sale" else f"Jama ({e.get_mode_display()})",
                "retailer": e.retailer.name,
                "salesman": e.salesman.full_name or e.salesman.username,
                "amount": str(e.amount),
                "notes": e.notes,
            })
        suffix = f"-{salesman.username}" if salesman else ""
        return _csv_response(
            f"daily-closing-{date.isoformat()}{suffix}.csv",
            ["when", "type", "retailer", "salesman", "amount", "notes"],
            rows,
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

    if _wants_csv(request):
        rows = []
        for b in buckets_ordered:
            for row in b["rows"]:
                rows.append({
                    "bucket": b["label"],
                    "retailer": row["retailer"].name,
                    "area": row["retailer"].area,
                    "baaki": str(row["baaki"]),
                    "age_days": row["age_days"],
                })
        suffix = f"-{salesman.username}" if salesman else ""
        return _csv_response(
            f"baaki-aging{suffix}-{now.date().isoformat()}.csv",
            ["bucket", "retailer", "area", "baaki", "age_days"],
            rows,
        )

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


# ---------------------------------------------------------------------------
# Salesman performance (admin only)
# ---------------------------------------------------------------------------


@admin_required
def salesman_performance(request):
    """Per-salesman activity over a date range.

    Default range: last 30 days. Optional `?start=YYYY-MM-DD&end=YYYY-MM-DD`.
    Each row: salesman, # entries (sales + payments), Udhar issued,
    Cash collected, UPI collected, # visits, current Outstanding Baaki.
    Sortable by any column client-side (small dataset; no server sort).
    """
    end = _parse_date(request.GET.get("end"))
    start_param = request.GET.get("start")
    start = _parse_date(start_param) if start_param else (end - timedelta(days=29))
    if start > end:
        start, end = end, start

    tz = timezone.get_current_timezone()
    start_dt = datetime.combine(start, datetime.min.time()).replace(tzinfo=tz)
    end_dt = datetime.combine(end, datetime.min.time()).replace(tzinfo=tz) + timedelta(days=1)

    rows = []
    for sm in User.objects.filter(role=User.Role.SALESMAN).order_by("-is_active", "full_name", "username"):
        sm_sales = Sale.objects.filter(
            salesman=sm, is_deleted=False,
            occurred_at__gte=start_dt, occurred_at__lt=end_dt,
        )
        sm_payments = Payment.objects.filter(
            salesman=sm, is_deleted=False,
            occurred_at__gte=start_dt, occurred_at__lt=end_dt,
        )
        sm_visits = Visit.objects.filter(
            salesman=sm,
            last_activity_at__gte=start_dt, last_activity_at__lt=end_dt,
        ).count()
        outstanding = (
            Retailer.objects.with_baaki(salesman=sm).aggregate(s=Sum("baaki"))["s"] or Decimal("0")
        )
        rows.append({
            "salesman": sm,
            "entries": sm_sales.count() + sm_payments.count(),
            "udhar": sm_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0"),
            "cash": sm_payments.filter(mode=Payment.Mode.CASH).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
            "upi": sm_payments.filter(mode=Payment.Mode.UPI).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
            "visits": sm_visits,
            "outstanding": outstanding,
        })

    if _wants_csv(request):
        csv_rows = [
            {
                "salesman": r["salesman"].full_name or r["salesman"].username,
                "username": r["salesman"].username,
                "entries": r["entries"],
                "udhar_issued": str(r["udhar"]),
                "cash_collected": str(r["cash"]),
                "upi_collected": str(r["upi"]),
                "visits": r["visits"],
                "outstanding_baaki_now": str(r["outstanding"]),
            }
            for r in rows
        ]
        return _csv_response(
            f"salesman-performance-{start.isoformat()}-to-{end.isoformat()}.csv",
            ["salesman", "username", "entries", "udhar_issued", "cash_collected", "upi_collected", "visits", "outstanding_baaki_now"],
            csv_rows,
        )

    return render(request, "dashboard/reports/salesman_performance.html", {
        "active": "reports",
        "report_name": "Salesman Performance",
        "start": start, "start_iso": start.isoformat(),
        "end": end, "end_iso": end.isoformat(),
        "rows": rows,
    })


# ---------------------------------------------------------------------------
# Retailer statement (admin)
# ---------------------------------------------------------------------------


@admin_required
def retailer_statement(request):
    """A printable per-retailer ledger over a date range with running Baaki.

    Admin path: any retailer, optionally scoped to one salesman.
    The HTML has a print stylesheet — "Save as PDF" via browser is the
    V1 export. CSV export available via ?format=csv.
    """
    retailer_pk = request.GET.get("retailer")
    salesman = _parse_salesman(request.GET.get("salesman"))
    end = _parse_date(request.GET.get("end"))
    start_param = request.GET.get("start")
    start = _parse_date(start_param) if start_param else (end - timedelta(days=29))
    if start > end:
        start, end = end, start

    retailer = None
    if retailer_pk:
        retailer = get_object_or_404(Retailer, pk=retailer_pk)

    all_retailers = Retailer.objects.filter(is_active=True).order_by("name")
    all_salesmen = User.objects.filter(role=User.Role.SALESMAN, is_active=True).order_by("full_name", "username")

    if retailer is None:
        # Just show the picker — no statement to render yet.
        return render(request, "dashboard/reports/retailer_statement.html", {
            "active": "reports",
            "report_name": "Retailer Statement",
            "all_retailers": all_retailers,
            "all_salesmen": all_salesmen,
            "retailer": None,
            "start": start, "start_iso": start.isoformat(),
            "end": end, "end_iso": end.isoformat(),
            "selected_salesman": salesman,
            "rows": [],
            "opening_baaki": Decimal("0"),
            "closing_baaki": Decimal("0"),
        })

    tz = timezone.get_current_timezone()
    start_dt = datetime.combine(start, datetime.min.time()).replace(tzinfo=tz)
    end_dt = datetime.combine(end, datetime.min.time()).replace(tzinfo=tz) + timedelta(days=1)

    # Opening Baaki — everything before `start`.
    def _scope(qs):
        return qs.filter(salesman=salesman) if salesman else qs

    pre_sales = _scope(Sale.objects.filter(retailer=retailer, is_deleted=False, occurred_at__lt=start_dt))
    pre_payments = _scope(Payment.objects.filter(retailer=retailer, is_deleted=False, occurred_at__lt=start_dt))
    opening_baaki = (
        (pre_sales.aggregate(s=Sum("amount"))["s"] or Decimal("0"))
        - (pre_payments.aggregate(s=Sum("amount"))["s"] or Decimal("0"))
    )

    # In-range entries — chronological (oldest first) for running balance.
    in_sales = _scope(Sale.objects.filter(
        retailer=retailer, is_deleted=False,
        occurred_at__gte=start_dt, occurred_at__lt=end_dt,
    ).select_related("salesman"))
    in_payments = _scope(Payment.objects.filter(
        retailer=retailer, is_deleted=False,
        occurred_at__gte=start_dt, occurred_at__lt=end_dt,
    ).select_related("salesman"))

    timeline = sorted(
        [("sale", s) for s in in_sales] + [("payment", p) for p in in_payments],
        key=lambda pair: pair[1].occurred_at,
    )

    running = opening_baaki
    rows = []
    for kind, e in timeline:
        if kind == "sale":
            running += e.amount
        else:
            running -= e.amount
        rows.append({"kind": kind, "entry": e, "running": running})

    closing_baaki = running

    if _wants_csv(request):
        csv_rows = [
            {
                "when": r["entry"].occurred_at.strftime("%Y-%m-%d %H:%M"),
                "type": "Udhar" if r["kind"] == "sale" else f"Jama ({r['entry'].get_mode_display()})",
                "amount": str(r["entry"].amount),
                "salesman": r["entry"].salesman.full_name or r["entry"].salesman.username,
                "notes": r["entry"].notes,
                "running_baaki": str(r["running"]),
            }
            for r in rows
        ]
        suffix = f"-{salesman.username}" if salesman else ""
        return _csv_response(
            f"statement-{retailer.name.replace(' ', '_').lower()}-{start.isoformat()}-to-{end.isoformat()}{suffix}.csv",
            ["when", "type", "amount", "salesman", "notes", "running_baaki"],
            csv_rows,
        )

    return render(request, "dashboard/reports/retailer_statement.html", {
        "active": "reports",
        "report_name": "Retailer Statement",
        "all_retailers": all_retailers,
        "all_salesmen": all_salesmen,
        "retailer": retailer,
        "start": start, "start_iso": start.isoformat(),
        "end": end, "end_iso": end.isoformat(),
        "selected_salesman": salesman,
        "rows": rows,
        "opening_baaki": opening_baaki,
        "closing_baaki": closing_baaki,
    })
