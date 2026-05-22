from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import (
    DecimalField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone


VISIT_GROUPING_WINDOW = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Retailer (the dukaan)
# ---------------------------------------------------------------------------


class RetailerQuerySet(models.QuerySet):
    """Custom queryset for Retailer with the Baaki annotation."""

    def with_baaki(self):
        sales = (
            Sale.objects.filter(retailer=OuterRef("pk"), is_deleted=False)
            .order_by()
            .values("retailer")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        payments = (
            Payment.objects.filter(retailer=OuterRef("pk"), is_deleted=False)
            .order_by()
            .values("retailer")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        zero = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2))
        return self.annotate(
            baaki=Coalesce(Subquery(sales, output_field=DecimalField()), zero)
            - Coalesce(Subquery(payments, output_field=DecimalField()), zero)
        )


class Retailer(models.Model):
    name = models.CharField(max_length=200)
    owner_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    area = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RetailerQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def current_baaki(self) -> Decimal:
        """Live Baaki for one retailer. Use Retailer.objects.with_baaki() for lists."""
        sales = self.sales.filter(is_deleted=False).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        payments = self.payments.filter(is_deleted=False).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        return sales - payments


# ---------------------------------------------------------------------------
# Visit (auto-grouped session at a retailer by a salesman)
# ---------------------------------------------------------------------------


class Visit(models.Model):
    salesman = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="visits",
    )
    retailer = models.ForeignKey(
        Retailer,
        on_delete=models.PROTECT,
        related_name="visits",
    )
    started_at = models.DateTimeField()
    last_activity_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity_at"]
        indexes = [
            models.Index(fields=["salesman", "retailer", "-last_activity_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.salesman} @ {self.retailer} ({self.started_at:%Y-%m-%d %H:%M})"

    @classmethod
    def attach(cls, *, salesman, retailer, occurred_at) -> "Visit":
        """Return the Visit a new Sale/Payment should belong to (see PLAN §3.5).

        Looks for the most-recent Visit by the same salesman at the same retailer
        whose last_activity_at is within VISIT_GROUPING_WINDOW before occurred_at.
        Otherwise creates a new Visit.
        """
        window_start = occurred_at - VISIT_GROUPING_WINDOW
        existing = (
            cls.objects.filter(
                salesman=salesman,
                retailer=retailer,
                last_activity_at__gte=window_start,
            )
            .order_by("-last_activity_at")
            .first()
        )
        if existing is not None:
            if occurred_at > existing.last_activity_at:
                existing.last_activity_at = occurred_at
                existing.save(update_fields=["last_activity_at", "updated_at"])
            return existing
        return cls.objects.create(
            salesman=salesman,
            retailer=retailer,
            started_at=occurred_at,
            last_activity_at=occurred_at,
        )


# ---------------------------------------------------------------------------
# Sale (an Udhar entry) and Payment (a Jama entry)
# ---------------------------------------------------------------------------


_AMOUNT_VALIDATORS = [MinValueValidator(Decimal("0.01"))]


class _LedgerEntry(models.Model):
    """Common fields and behavior for Sale and Payment."""

    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        related_name="%(class)ss",
    )
    retailer = models.ForeignKey(
        Retailer,
        on_delete=models.PROTECT,
        related_name="%(class)ss",
    )
    salesman = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="%(class)ss",
    )
    amount = models.DecimalField(
        max_digits=11,
        decimal_places=2,
        validators=_AMOUNT_VALIDATORS,
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-occurred_at"]

    def save(self, *args, **kwargs):
        # Auto-attach to a Visit on first save when one isn't explicitly set.
        if self.visit_id is None and self.salesman_id and self.retailer_id:
            occurred_at = self.occurred_at or timezone.now()
            self.visit = Visit.attach(
                salesman=self.salesman,
                retailer=self.retailer,
                occurred_at=occurred_at,
            )
        super().save(*args, **kwargs)


class Sale(_LedgerEntry):
    """Recharge given to a retailer (Udhar)."""

    class Meta(_LedgerEntry.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="sale_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"Udhar ₹{self.amount} → {self.retailer}"


class Payment(_LedgerEntry):
    """Money received from a retailer (Jama)."""

    class Mode(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"

    mode = models.CharField(max_length=8, choices=Mode.choices)

    class Meta(_LedgerEntry.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"Jama ₹{self.amount} ({self.get_mode_display()}) ← {self.retailer}"


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_actions",
    )
    entity_type = models.CharField(max_length=64)
    entity_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=16, choices=Action.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["-at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}#{self.entity_id} by {self.actor or 'system'} at {self.at:%Y-%m-%d %H:%M}"
