"""
Tests for the Phase 1 money-handling code. Covers the paths the reviewer
flagged as load-bearing: Visit auto-grouping window, Baaki excludes
soft-deleted entries, deleted_reason enforcement.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from .audit import snapshot
from .models import Payment, Retailer, Sale, Visit


User = get_user_model()


def _fresh_user(username="testsalesman"):
    return User.objects.create_user(
        username=username,
        password="x",
        full_name="Test Salesman",
        role=User.Role.SALESMAN,
    )


def _fresh_retailer(name="Test Dukaan"):
    return Retailer.objects.create(name=name, area="Test Area")


class VisitAttachTests(TestCase):
    """The 15-minute auto-grouping rule from PLAN §3.5."""

    def setUp(self):
        self.salesman = _fresh_user()
        self.retailer = _fresh_retailer()

    def test_first_entry_creates_visit(self):
        Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100")
        )
        self.assertEqual(Visit.objects.count(), 1)

    def test_entries_within_window_share_a_visit(self):
        now = timezone.now()
        s1 = Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100"),
            occurred_at=now,
        )
        s2 = Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("200"),
            occurred_at=now + timedelta(minutes=10),
        )
        p1 = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("50"),
            mode=Payment.Mode.CASH,
            occurred_at=now + timedelta(minutes=14, seconds=59),
        )
        self.assertEqual(Visit.objects.count(), 1)
        self.assertEqual(s1.visit_id, s2.visit_id)
        self.assertEqual(p1.visit_id, s1.visit_id)

    def test_entries_outside_window_create_new_visit(self):
        now = timezone.now()
        Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100"),
            occurred_at=now,
        )
        Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("200"),
            occurred_at=now + timedelta(minutes=15, seconds=1),
        )
        self.assertEqual(Visit.objects.count(), 2)

    def test_different_retailer_creates_separate_visit(self):
        other = _fresh_retailer("Other Dukaan")
        now = timezone.now()
        Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100"),
            occurred_at=now,
        )
        Sale.objects.create(
            salesman=self.salesman, retailer=other, amount=Decimal("200"),
            occurred_at=now + timedelta(minutes=1),
        )
        self.assertEqual(Visit.objects.count(), 2)


class BaakiTests(TestCase):
    """Σ Sale.amount − Σ Payment.amount, excluding soft-deleted rows."""

    def setUp(self):
        self.salesman = _fresh_user()
        self.retailer = _fresh_retailer()

    def _baaki(self):
        return Retailer.objects.with_baaki().get(pk=self.retailer.pk).baaki

    def test_zero_baaki_initially(self):
        self.assertEqual(self._baaki(), Decimal("0"))
        self.assertEqual(self.retailer.current_baaki, Decimal("0"))

    def test_baaki_sums_sales_and_subtracts_payments(self):
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("5000"))
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("2000"))
        Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("3000"),
            mode=Payment.Mode.UPI,
        )
        self.assertEqual(self._baaki(), Decimal("4000"))
        self.assertEqual(self.retailer.current_baaki, Decimal("4000"))

    def test_baaki_excludes_soft_deleted_sales(self):
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("5000"))
        s2 = Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("2000"))
        s2.is_deleted = True
        s2.deleted_reason = "Test reversal"
        s2.save()
        self.assertEqual(self._baaki(), Decimal("5000"))

    def test_baaki_excludes_soft_deleted_payments(self):
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("5000"))
        p = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("3000"),
            mode=Payment.Mode.CASH,
        )
        p.is_deleted = True
        p.deleted_reason = "Test"
        p.save()
        self.assertEqual(self._baaki(), Decimal("5000"))

    def test_overpayment_goes_negative(self):
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("1000"))
        Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("1500"),
            mode=Payment.Mode.CASH,
        )
        self.assertEqual(self._baaki(), Decimal("-500"))


class DeletedReasonEnforcementTests(TestCase):
    """is_deleted=True requires a non-empty deleted_reason (PLAN §3)."""

    def setUp(self):
        self.salesman = _fresh_user()
        self.retailer = _fresh_retailer()
        self.sale = Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100")
        )

    def test_clean_blocks_delete_without_reason(self):
        self.sale.is_deleted = True
        self.sale.deleted_reason = ""
        with self.assertRaises(ValidationError):
            self.sale.full_clean()

    def test_clean_blocks_whitespace_only_reason(self):
        self.sale.is_deleted = True
        self.sale.deleted_reason = "   "
        with self.assertRaises(ValidationError):
            self.sale.full_clean()

    def test_clean_passes_with_reason(self):
        self.sale.is_deleted = True
        self.sale.deleted_reason = "Wrong amount entered"
        self.sale.full_clean()  # should not raise

    def test_db_constraint_blocks_delete_without_reason(self):
        """Belt-and-suspenders: DB-level CHECK fires even if clean() is skipped."""
        self.sale.is_deleted = True
        self.sale.deleted_reason = ""
        with self.assertRaises(IntegrityError):
            self.sale.save()


class AuditSnapshotTests(TestCase):
    """FK fields must be captured as ids so renames don't lose forensic trail."""

    def setUp(self):
        self.salesman = _fresh_user()
        self.retailer = _fresh_retailer()

    def test_snapshot_captures_fk_id_and_repr(self):
        sale = Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100")
        )
        snap = snapshot(sale)
        self.assertEqual(snap["retailer_id"], self.retailer.pk)
        self.assertEqual(snap["salesman_id"], self.salesman.pk)
        # Human-readable label is kept alongside for forensic readability.
        self.assertEqual(snap["retailer"], str(self.retailer))

    def test_snapshot_survives_retailer_rename(self):
        sale = Sale.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("100")
        )
        snap_before = snapshot(sale)
        # Rename the retailer; the id-based reference must still resolve.
        self.retailer.name = "Renamed Dukaan"
        self.retailer.save()
        self.assertEqual(
            Retailer.objects.get(pk=snap_before["retailer_id"]).pk,
            self.retailer.pk,
        )
