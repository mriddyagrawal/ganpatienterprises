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
        self.assertEqual(self.retailer.baaki_for(None), Decimal("0"))

    def test_baaki_sums_sales_and_subtracts_payments(self):
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("5000"))
        Sale.objects.create(salesman=self.salesman, retailer=self.retailer, amount=Decimal("2000"))
        Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer, amount=Decimal("3000"),
            mode=Payment.Mode.UPI,
        )
        self.assertEqual(self._baaki(), Decimal("4000"))
        self.assertEqual(self.retailer.baaki_for(None), Decimal("4000"))

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


class BaakiScopingTests(TestCase):
    """Per-salesman Baaki — the core data-scoping invariant (PLAN §1, §3)."""

    def setUp(self):
        self.s1 = _fresh_user("salesman_one")
        self.s2 = _fresh_user("salesman_two")
        self.retailer = _fresh_retailer()
        # s1's slice: ₹5000 sold − ₹1000 received = ₹4000 owed to s1
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("5000"))
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"),
            mode=Payment.Mode.CASH,
        )
        # s2's slice: ₹3000 sold − ₹0 received = ₹3000 owed to s2
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("3000"))

    def test_global_baaki_sums_all_salesmen(self):
        annotated = Retailer.objects.with_baaki().get(pk=self.retailer.pk)
        self.assertEqual(annotated.baaki, Decimal("7000"))
        self.assertEqual(self.retailer.baaki_for(None), Decimal("7000"))
        self.assertEqual(self.retailer.baaki_for(None), Decimal("7000"))

    def test_baaki_for_salesman_one(self):
        annotated = Retailer.objects.with_baaki(salesman=self.s1).get(pk=self.retailer.pk)
        self.assertEqual(annotated.baaki, Decimal("4000"))
        self.assertEqual(self.retailer.baaki_for(self.s1), Decimal("4000"))

    def test_baaki_for_salesman_two(self):
        annotated = Retailer.objects.with_baaki(salesman=self.s2).get(pk=self.retailer.pk)
        self.assertEqual(annotated.baaki, Decimal("3000"))
        self.assertEqual(self.retailer.baaki_for(self.s2), Decimal("3000"))

    def test_baaki_zero_for_salesman_with_no_history(self):
        s3 = _fresh_user("salesman_three")
        annotated = Retailer.objects.with_baaki(salesman=s3).get(pk=self.retailer.pk)
        self.assertEqual(annotated.baaki, Decimal("0"))
        self.assertEqual(self.retailer.baaki_for(s3), Decimal("0"))

    def test_scoped_baaki_excludes_other_salesmens_soft_deleted_entries(self):
        # s1 soft-deletes their payment; scope to s1 should shift accordingly,
        # but s2's view must be unaffected.
        p = Payment.objects.get(salesman=self.s1)
        p.is_deleted = True
        p.deleted_reason = "Test"
        p.save()
        self.assertEqual(self.retailer.baaki_for(self.s1), Decimal("5000"))
        self.assertEqual(self.retailer.baaki_for(self.s2), Decimal("3000"))


# ---------------------------------------------------------------------------
# Phase 2 view tests
# ---------------------------------------------------------------------------


class _ViewBase(TestCase):
    """Shared setup for view tests: two salesmen + one admin + a retailer."""

    @classmethod
    def setUpTestData(cls):
        cls.s1 = User.objects.create_user(
            username="s1", password="x", full_name="Salesman One",
            role=User.Role.SALESMAN,
        )
        cls.s2 = User.objects.create_user(
            username="s2", password="x", full_name="Salesman Two",
            role=User.Role.SALESMAN,
        )
        cls.admin = User.objects.create_user(
            username="adm", password="x", full_name="Owner",
            role=User.Role.ADMIN, is_staff=True, is_superuser=True,
        )
        cls.retailer = Retailer.objects.create(name="Mobile Shoppy", area="Market")
        cls.other_retailer = Retailer.objects.create(name="Sharma Mobile", area="Market")

    def login(self, user):
        self.client.force_login(user)


class RoleGuardTests(_ViewBase):
    def test_anonymous_redirected_to_login(self):
        for url in ["/", "/aaj/", f"/dukaan/{self.retailer.pk}/", "/entry/new/"]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("/login/", resp["Location"], url)

    def test_admin_bounced_from_dukaan_root_to_admin_panel(self):
        self.login(self.admin)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/admin/")

    def test_admin_bounced_from_salesman_views(self):
        self.login(self.admin)
        resp = self.client.get("/aaj/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/admin/")

    def test_salesman_can_see_dukaan(self):
        self.login(self.s1)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mobile Shoppy")


class DukaanListTests(_ViewBase):
    def test_baaki_column_is_scoped_to_logged_in_salesman(self):
        # s1: ₹5000 udhar, s2: ₹3000 udhar at same retailer
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("5000"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("3000"))

        self.login(self.s1)
        resp = self.client.get("/")
        self.assertContains(resp, "5,000")
        self.assertNotContains(resp, "3,000")  # s2's amount is invisible to s1

        self.login(self.s2)
        resp = self.client.get("/")
        self.assertContains(resp, "3,000")
        self.assertNotContains(resp, "5,000")

    def test_search_filters_by_name(self):
        self.login(self.s1)
        resp = self.client.get("/?q=Sharma")
        self.assertContains(resp, "Sharma Mobile")
        self.assertNotContains(resp, "Mobile Shoppy")


class RetailerDetailTests(_ViewBase):
    def setUp(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"), notes="s1-note")
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("2000"), notes="s2-note")

    def test_timeline_shows_only_logged_in_salesmans_entries(self):
        self.login(self.s1)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "s1-note")
        self.assertNotContains(resp, "s2-note")

    def test_baaki_card_is_scoped(self):
        self.login(self.s2)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/")
        self.assertContains(resp, "2,000")
        self.assertNotContains(resp, "1,000")


class EntryNewTests(_ViewBase):
    def test_create_udhar(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"kind": "udhar", "amount": "500", "notes": "phase-2-test-udhar"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], f"/dukaan/{self.retailer.pk}/")
        sale = Sale.objects.get(notes="phase-2-test-udhar")
        self.assertEqual(sale.salesman, self.s1)
        self.assertEqual(sale.amount, Decimal("500"))
        # Visit auto-attached
        self.assertIsNotNone(sale.visit_id)
        # Audit logged
        from .models import AuditLog
        self.assertTrue(
            AuditLog.objects.filter(entity_type="Sale", entity_id=sale.pk, action="create").exists()
        )

    def test_create_jama_with_mode(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"kind": "jama", "amount": "200", "mode": "upi", "notes": "phase-2-test-jama"},
        )
        self.assertEqual(resp.status_code, 302)
        payment = Payment.objects.get(notes="phase-2-test-jama")
        self.assertEqual(payment.mode, "upi")

    def test_jama_without_mode_rejected(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"kind": "jama", "amount": "200", "notes": "no-mode"},
        )
        self.assertEqual(resp.status_code, 200)  # re-renders form with errors
        self.assertFalse(Payment.objects.filter(notes="no-mode").exists())

    def test_entry_new_with_missing_kind_shows_error(self):
        """A POST without a `kind` (tampered hidden input) must surface the
        problem instead of silently re-rendering an empty form."""
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"amount": "100", "notes": "no-kind-test"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Udhar ya Jama")
        self.assertFalse(Sale.objects.filter(notes="no-kind-test").exists())


class HtmxLiveSearchTests(_ViewBase):
    """HTMX requests return just the results partial, not the full page."""

    def setUp(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"))

    def test_dukaan_htmx_returns_partial(self):
        self.login(self.s1)
        resp = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        # Partial has no <html> / <body> wrapper from the salesman base.
        body = resp.content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("<nav", body)
        self.assertIn("Mobile Shoppy", body)

    def test_dukaan_non_htmx_returns_full_page(self):
        self.login(self.s1)
        resp = self.client.get("/")
        body = resp.content.decode()
        self.assertIn("<html", body)

    def test_entry_picker_htmx_returns_partial(self):
        self.login(self.s1)
        resp = self.client.get("/entry/new/", HTTP_HX_REQUEST="true")
        body = resp.content.decode()
        self.assertNotIn("<html", body)
        self.assertIn("Mobile Shoppy", body)


class EntryEditDeleteTests(_ViewBase):
    def setUp(self):
        self.sale = Sale.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("500"),
        )

    def test_edit_within_24h_works(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/edit/",
            {"amount": "750", "notes": "edited"},
        )
        self.assertEqual(resp.status_code, 302)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount, Decimal("750"))

    def test_edit_other_salesmans_entry_returns_404(self):
        self.login(self.s2)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/edit/",
            {"amount": "750"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_edit_after_24h_forbidden(self):
        # Backdate the sale by tampering with created_at.
        Sale.objects.filter(pk=self.sale.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/edit/",
            {"amount": "750"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_requires_reason(self):
        self.login(self.s1)
        # Empty reason → form re-renders, not deleted
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/delete/",
            {"reason": "   "},
        )
        self.assertEqual(resp.status_code, 200)
        self.sale.refresh_from_db()
        self.assertFalse(self.sale.is_deleted)

    def test_delete_with_reason_soft_deletes(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/delete/",
            {"reason": "Wrong amount"},
        )
        self.assertEqual(resp.status_code, 302)
        self.sale.refresh_from_db()
        self.assertTrue(self.sale.is_deleted)
        self.assertEqual(self.sale.deleted_reason, "Wrong amount")


class AajReportTests(_ViewBase):
    def test_today_numbers_scoped_to_logged_in_salesman(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("100"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("9999"))
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("50"),
            mode=Payment.Mode.CASH,
        )

        self.login(self.s1)
        resp = self.client.get("/aaj/")
        self.assertEqual(resp.status_code, 200)
        # s1's own Udhar (100) appears; s2's 9999 must not be reported as s1's
        self.assertContains(resp, "₹100")
        self.assertNotContains(resp, "9,999")


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
