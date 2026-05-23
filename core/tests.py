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
        # In the new (Phase C) flow, the salesman Dukaan list is filtered to
        # `assigned_salesman = request.user`. Existing view tests expect to
        # see both fixture retailers in s1's view, so we assign both to s1
        # at setup time. Tests that exercise multi-salesman scoping reassign
        # `other_retailer` to s2 inline.
        cls.retailer = Retailer.objects.create(
            name="Mobile Shoppy", area="Market",
            assigned_salesman=cls.s1,
        )
        cls.other_retailer = Retailer.objects.create(
            name="Sharma Mobile", area="Market",
            assigned_salesman=cls.s1,
        )

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
        self.assertEqual(resp["Location"], "/dashboard/")

    def test_admin_bounced_from_salesman_views(self):
        self.login(self.admin)
        resp = self.client.get("/aaj/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/dashboard/")

    def test_salesman_can_see_dukaan(self):
        self.login(self.s1)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mobile Shoppy")


class DukaanListTests(_ViewBase):
    def test_baaki_column_is_scoped_to_logged_in_salesman(self):
        """Per Phase C, the Dukaan list filters by `assigned_salesman`.
        Each salesman sees their own retailer with their own Baaki."""
        # Assign Mobile Shoppy to s1 and Sharma Mobile to s2 (override the
        # _ViewBase default that assigns both to s1).
        self.retailer.assigned_salesman = self.s1
        self.retailer.save(update_fields=["assigned_salesman"])
        self.other_retailer.assigned_salesman = self.s2
        self.other_retailer.save(update_fields=["assigned_salesman"])

        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("5000"))
        Sale.objects.create(salesman=self.s2, retailer=self.other_retailer, amount=Decimal("3000"))

        self.login(self.s1)
        resp = self.client.get("/")
        # Sees own retailer + Baaki.
        self.assertContains(resp, "Mobile Shoppy")
        self.assertContains(resp, "5,000")
        # s2's retailer is invisible.
        self.assertNotContains(resp, "Sharma Mobile")
        self.assertNotContains(resp, "3,000")

        self.login(self.s2)
        resp = self.client.get("/")
        self.assertContains(resp, "Sharma Mobile")
        self.assertContains(resp, "3,000")
        self.assertNotContains(resp, "Mobile Shoppy")
        self.assertNotContains(resp, "5,000")

    def test_search_filters_by_name(self):
        """Search runs inside the assigned-retailer scope. Both fixtures
        belong to s1 so both are searchable from s1's session."""
        self.login(self.s1)
        resp = self.client.get("/?q=Sharma")
        self.assertContains(resp, "Sharma Mobile")
        self.assertNotContains(resp, "Mobile Shoppy")

    def test_unassigned_salesman_sees_empty_dukaan(self):
        """A salesman with no assigned retailers sees the empty state,
        not other salesmen's retailers."""
        loner = User.objects.create_user(
            username="loner", password="x",
            role=User.Role.SALESMAN,
        )
        self.login(loner)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Mobile Shoppy")
        self.assertNotContains(resp, "Sharma Mobile")
        self.assertContains(resp, "Koi dukaan nahi hai")


class RetailerDetailTests(_ViewBase):
    def setUp(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"), notes="s1-note")
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("2000"), notes="s2-note")

    def test_timeline_shows_only_logged_in_salesmans_entries(self):
        """`retailer` is assigned to s1 in `_ViewBase.setUpTestData`. When s1
        opens it, his own notes show; s2's notes don't."""
        self.login(self.s1)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "s1-note")
        self.assertNotContains(resp, "s2-note")

    def test_baaki_card_is_scoped_when_retailer_reassigned(self):
        """When the retailer is reassigned to s2, s2 sees only s2's slice
        of Baaki on the detail page — s1's entries are excluded."""
        self.retailer.assigned_salesman = self.s2
        self.retailer.save(update_fields=["assigned_salesman"])
        self.login(self.s2)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2,000")
        self.assertNotContains(resp, "1,000")

    def test_unassigned_salesman_gets_404_on_retailer_detail(self):
        """Strict mode (Phase C followup): a salesman who isn't the
        retailer's `assigned_salesman` gets a 404 when hitting the URL
        directly. Cross-coverage is captured in futureplans #10."""
        # `retailer` is assigned to s1 (per _ViewBase); s2 is not.
        self.login(self.s2)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_unassigned_salesman_gets_404_on_entry_new(self):
        """Same strict mode applied to the Jama entry form."""
        self.login(self.s2)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"amount": "100", "mode": "cash", "notes": "should-not-create"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Payment.objects.filter(notes="should-not-create").exists())

    def test_entry_picker_filtered_to_assigned_retailers(self):
        """The Naya Entry retailer picker only lists retailers assigned
        to the logged-in salesman."""
        self.other_retailer.assigned_salesman = self.s2
        self.other_retailer.save(update_fields=["assigned_salesman"])
        # Now: `retailer` is s1's, `other_retailer` is s2's.

        self.login(self.s1)
        resp = self.client.get("/entry/new/")
        body = resp.content.decode()
        self.assertIn("Mobile Shoppy", body)
        self.assertNotIn("Sharma Mobile", body)

        self.login(self.s2)
        resp = self.client.get("/entry/new/")
        body = resp.content.decode()
        self.assertNotIn("Mobile Shoppy", body)
        self.assertIn("Sharma Mobile", body)


class EntryNewTests(_ViewBase):
    def test_create_jama_creates_payment_and_visit(self):
        """Phase C: salesman flow is Jama-only. Any POST to the entry
        endpoint creates a Payment; `kind` in the form data is ignored."""
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"amount": "500", "mode": "cash", "notes": "phase-c-jama"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], f"/dukaan/{self.retailer.pk}/")
        payment = Payment.objects.get(notes="phase-c-jama")
        self.assertEqual(payment.salesman, self.s1)
        self.assertEqual(payment.amount, Decimal("500"))
        self.assertEqual(payment.mode, "cash")
        # Visit auto-attached
        self.assertIsNotNone(payment.visit_id)
        # Audit logged
        from .models import AuditLog
        self.assertTrue(
            AuditLog.objects.filter(entity_type="Payment", entity_id=payment.pk, action="create").exists()
        )

    def test_create_jama_with_upi_mode(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"amount": "200", "mode": "upi", "notes": "phase-c-test-jama-upi"},
        )
        self.assertEqual(resp.status_code, 302)
        payment = Payment.objects.get(notes="phase-c-test-jama-upi")
        self.assertEqual(payment.mode, "upi")

    def test_jama_without_mode_rejected(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"amount": "200", "notes": "no-mode"},
        )
        self.assertEqual(resp.status_code, 200)  # re-renders form with errors
        self.assertFalse(Payment.objects.filter(notes="no-mode").exists())

    def test_udhar_post_does_not_create_a_sale(self):
        """A tampered POST that looks like the old Udhar form (kind=udhar,
        no `mode`) must NOT create a Sale. The view runs the Jama form,
        which rejects it because `mode` is missing."""
        self.login(self.s1)
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            {"kind": "udhar", "amount": "500", "notes": "should-not-create-sale"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Sale.objects.filter(notes="should-not-create-sale").exists())
        self.assertFalse(Payment.objects.filter(notes="should-not-create-sale").exists())

    def test_phase_a_jio_fields_exist(self):
        """The schema additions for the Jio import pipeline are in place."""
        from core.models import Retailer, Sale

        # Retailer: jio_partner_id + assigned_salesman FK
        r = Retailer.objects.create(
            name="Phase A Test",
            jio_partner_id="0660000999",
            assigned_salesman=self.s1,
        )
        self.assertEqual(r.jio_partner_id, "0660000999")
        self.assertEqual(r.assigned_salesman, self.s1)

        # Sale: jio_order_id + face_value
        s = Sale.objects.create(
            salesman=self.s1, retailer=r,
            amount=Decimal("3000.00"),
            face_value=Decimal("3090.00"),
            jio_order_id="2615011858",
        )
        self.assertEqual(s.jio_order_id, "2615011858")
        self.assertEqual(s.face_value, Decimal("3090.00"))

        # User: jio_fos_id
        from accounts.models import User as UserModel
        u = UserModel.objects.create_user(
            username="fos-test", password="x",
            role=UserModel.Role.SALESMAN, jio_fos_id="0691060999",
        )
        self.assertEqual(u.jio_fos_id, "0691060999")

    def test_phase_a_jio_order_id_unique(self):
        """jio_order_id is the idempotency key — same value on two Sales
        must violate the unique constraint."""
        from django.db import IntegrityError
        from core.models import Sale

        Sale.objects.create(
            salesman=self.s1, retailer=self.retailer,
            amount=Decimal("3000"), jio_order_id="DUP-ID-1",
        )
        with self.assertRaises(IntegrityError):
            Sale.objects.create(
                salesman=self.s1, retailer=self.retailer,
                amount=Decimal("3000"), jio_order_id="DUP-ID-1",
            )

    def test_phase_a_jio_partner_id_unique(self):
        """Two Retailers can't share a jio_partner_id."""
        from django.db import IntegrityError
        from core.models import Retailer as RetailerModel

        RetailerModel.objects.create(name="A", jio_partner_id="PID-100")
        with self.assertRaises(IntegrityError):
            RetailerModel.objects.create(name="B", jio_partner_id="PID-100")

    def test_phase_b_parse_normalizes_double_space_headers(self):
        """Jio's headers have `Partner  PRM ID` (two spaces); normalizer
        must collapse whitespace so the importer can find the column."""
        from core.jio_import import _normalize_header
        self.assertEqual(_normalize_header("Partner  PRM ID"), "partner_prm_id")
        self.assertEqual(_normalize_header("  FOS Name  "), "fos_name")
        self.assertEqual(_normalize_header("Order Date"), "order_date")

    def test_phase_b_parse_tsv_with_leading_blank_row(self):
        """The real Jio export is tab-separated with a blank leading
        row. Parser handles both."""
        from pathlib import Path
        from core.jio_import import parse_file_content
        fixture = (
            Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv"
        ).read_bytes()
        rows = parse_file_content(fixture)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["order_id"], "TST-ORDER-001")
        self.assertEqual(rows[0]["partner_prm_id"], "0660000001")  # leading zero preserved
        self.assertEqual(rows[0]["fos_name"], "Test FOS One")  # leading space stripped
        # Amounts come through with whatever trailing whitespace Jio gave us,
        # already stripped by the parser.
        self.assertEqual(rows[0]["order_amount"], "3090.000")

    def test_phase_b_parse_csv(self):
        """Parser also handles plain CSV (comma-delimited)."""
        from core.jio_import import parse_file_content
        content = (
            "\nOrder ID,Order Date,Order Time,Order Type,Partner  PRM ID,Partner  Name,"
            "Order Amount,Transfer Amount,Order Status,FOS ID,FOS Name\n"
            "ORD-1,21.05.2026,170055,AUTO,P-1,SHOP ONE,3090,3090,Completed,F-1,Salesman A\n"
        ).encode("utf-8")
        rows = parse_file_content(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "ORD-1")

    def test_phase_b_parse_xlsx(self):
        """Parser handles real .xlsx via openpyxl."""
        import io
        from openpyxl import Workbook
        from core.jio_import import parse_file_content

        wb = Workbook()
        ws = wb.active
        ws.append([])  # blank leading row
        ws.append([
            "Order ID", "Order Date", "Order Time", "Order Type",
            "Partner  PRM ID", "Partner  Name",
            "Order Amount", "Transfer Amount", "Order Status",
            "FOS ID", "FOS Name",
        ])
        ws.append([
            "ORD-XLSX-1", "21.05.2026", "170055", "AUTO",
            "P-X", "XLSX SHOP",
            3090, 3090, "Completed",
            "F-X", "Salesman X",
        ])
        buf = io.BytesIO()
        wb.save(buf)
        rows = parse_file_content(buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "ORD-XLSX-1")

    def test_phase_b_row_validation_filters(self):
        """Pending and non-AUTO rows are skipped with a readable reason."""
        from core.jio_import import validate_rows, parse_file_content
        from pathlib import Path
        fixture = (
            Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv"
        ).read_bytes()
        raw = parse_file_content(fixture)
        rows, errors = validate_rows(raw)
        # 5 raw rows: 3 valid (1 AUTO+Completed × 3), 1 Pending, 1 MANUAL.
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(errors), 2)
        # Errors should mention the skipped order IDs.
        self.assertTrue(any("TST-ORDER-004" in e for e in errors))
        self.assertTrue(any("TST-ORDER-005" in e for e in errors))

    def test_phase_b_amount_computed_from_face_value(self):
        """3% incentive: amount = face_value / 1.03, rounded to 2 places."""
        from decimal import Decimal
        from core.jio_import import validate_rows, parse_file_content
        from pathlib import Path
        raw = parse_file_content(
            (Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv").read_bytes()
        )
        rows, _ = validate_rows(raw)
        # First row: face_value 3090.000 → amount 3000.00
        self.assertEqual(rows[0].face_value, Decimal("3090.000"))
        self.assertEqual(rows[0].amount, Decimal("3000.00"))
        # Second: 5150 → 5000
        self.assertEqual(rows[1].amount, Decimal("5000.00"))

    def test_phase_b_time_padding(self):
        """Order Time `40005` → 04:00:05 (zero-padded to 6 digits)."""
        from core.jio_import import validate_rows, parse_file_content
        from pathlib import Path
        raw = parse_file_content(
            (Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv").read_bytes()
        )
        rows, _ = validate_rows(raw)
        # Third row's time was 40005 in the fixture.
        third = [r for r in rows if r.order_id == "TST-ORDER-003"][0]
        self.assertEqual(third.occurred_at.hour, 4)
        self.assertEqual(third.occurred_at.minute, 0)
        self.assertEqual(third.occurred_at.second, 5)

    def test_phase_b_apply_creates_sales_retailers_users(self):
        """Full import flow: 3 valid rows produce 3 Sales, 2 new retailers,
        2 new salesmen (the fixture's order #3 reuses retailer #1 and FOS #1)."""
        from core.jio_import import apply_plan, plan_import, validate_rows, parse_file_content
        from pathlib import Path
        raw = parse_file_content(
            (Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv").read_bytes()
        )
        rows, _ = validate_rows(raw)
        plan = plan_import(rows)
        self.assertEqual(plan.sales_to_create, 3)
        self.assertEqual(len(plan.new_retailers), 2)  # P-1 (orders 1,3) + P-2 (order 2)
        self.assertEqual(len(plan.new_salesmen), 2)  # F-1 (orders 1,3) + F-2 (order 2)

        result = apply_plan(plan, self.admin)
        self.assertEqual(result.created_sales, 3)
        self.assertEqual(result.created_retailers, 2)
        self.assertEqual(result.created_salesmen, 2)

        # Auto-created salesmen are inactive.
        from accounts.models import User as UserModel
        new_fos = UserModel.objects.get(jio_fos_id="0691000001")
        self.assertFalse(new_fos.is_active)
        self.assertEqual(new_fos.username, "fos-0691000001")
        self.assertEqual(new_fos.full_name, "Test FOS One")

        # Auto-created retailer gets its assigned_salesman from the first
        # row that introduced it.
        from core.models import Retailer as RetailerModel
        new_retailer = RetailerModel.objects.get(jio_partner_id="0660000001")
        self.assertEqual(new_retailer.assigned_salesman, new_fos)

        # The Sale rows have face_value and the divided-by-1.03 amount.
        from core.models import Sale
        s = Sale.objects.get(jio_order_id="TST-ORDER-001")
        self.assertEqual(s.amount, Decimal("3000.00"))
        self.assertEqual(s.face_value, Decimal("3090.000"))

    def test_phase_b_apply_does_not_create_visit_rows(self):
        """AUTO refills aren't physical visits — the importer must skip
        `Visit.attach`, otherwise the salesman's "Aaj N dukaan visit
        kiye" counter and the admin's visit stats inflate with phantom
        rows the salesman never made."""
        from core.jio_import import apply_plan, plan_import, validate_rows, parse_file_content
        from core.models import Visit
        from pathlib import Path

        visits_before = Visit.objects.count()
        raw = parse_file_content(
            (Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv").read_bytes()
        )
        rows, _ = validate_rows(raw)
        result = apply_plan(plan_import(rows), self.admin)
        self.assertEqual(result.created_sales, 3)
        self.assertEqual(Visit.objects.count(), visits_before)  # nothing added
        # And the Sales themselves have visit=None.
        from core.models import Sale
        for sale in Sale.objects.filter(jio_order_id__startswith="TST-ORDER-"):
            self.assertIsNone(sale.visit_id, f"Imported Sale {sale.jio_order_id} has a Visit")

    def test_phase_b_apply_is_idempotent(self):
        """Re-running the same import is safe — second run creates 0 Sales."""
        from core.jio_import import apply_plan, plan_import, validate_rows, parse_file_content
        from pathlib import Path
        raw = parse_file_content(
            (Path(__file__).parent / "tests_fixtures" / "jio_sample.tsv").read_bytes()
        )
        rows, _ = validate_rows(raw)
        apply_plan(plan_import(rows), self.admin)  # first run

        # Second run: plan should see all duplicates.
        plan2 = plan_import(rows)
        self.assertEqual(plan2.sales_to_create, 0)
        self.assertEqual(plan2.skipped_duplicates, 3)
        result2 = apply_plan(plan2, self.admin)
        self.assertEqual(result2.created_sales, 0)
        self.assertEqual(result2.skipped_duplicates, 3)

    def test_phase_c_flush_transactions_command(self):
        """`manage.py flush_transactions --yes` removes all Sale/Payment/
        Visit/AuditLog rows while leaving Users and Retailers intact."""
        from io import StringIO
        from django.core.management import call_command
        from core.models import AuditLog, Payment, Sale, Visit
        from accounts.models import User as UserModel

        # Populate some transactions to wipe.
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"))
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
        )
        self.assertGreater(Sale.objects.count(), 0)
        self.assertGreater(Payment.objects.count(), 0)
        self.assertGreater(Visit.objects.count(), 0)

        users_before = UserModel.objects.count()
        retailers_before = Retailer.objects.count()

        out = StringIO()
        call_command("flush_transactions", "--yes", stdout=out)

        # Transactions gone.
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Visit.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        # Users and Retailers untouched.
        self.assertEqual(UserModel.objects.count(), users_before)
        self.assertEqual(Retailer.objects.count(), retailers_before)
        # Command output mentions what it did.
        self.assertIn("Deleted", out.getvalue())

    def test_phase_c_flush_transactions_when_already_empty(self):
        """Running flush on an empty DB short-circuits cleanly."""
        from io import StringIO
        from django.core.management import call_command
        from core.models import AuditLog, Payment, Sale, Visit

        AuditLog.objects.all().delete()
        # Visit has PROTECT on Sale/Payment, so delete those first
        Sale.objects.all().delete()
        Payment.objects.all().delete()
        Visit.objects.all().delete()

        out = StringIO()
        call_command("flush_transactions", "--yes", stdout=out)
        self.assertIn("already empty", out.getvalue())

    def test_phase_a_jio_partner_id_nullable_allows_many_blanks(self):
        """Existing manually-entered retailers without a jio_partner_id
        can coexist — unique=True with null=True doesn't reject multiple
        NULLs in PostgreSQL/SQLite."""
        from core.models import Retailer as RetailerModel
        # Note: setUpTestData already creates retailers without jio_partner_id.
        # Adding another should work fine.
        RetailerModel.objects.create(name="Another", jio_partner_id=None)
        # Two with None should be allowed
        self.assertGreaterEqual(
            RetailerModel.objects.filter(jio_partner_id__isnull=True).count(), 1,
        )

    def test_payment_form_mode_has_no_blank_choice(self):
        """Regression: Django adds a blank ('', '---------') row to a
        required CharField+choices, which rendered as a phantom third
        radio mislabeled "UPI" in the salesman Jama form. The form
        constructor now strips it."""
        from .forms import PaymentForm
        choices = list(PaymentForm().fields["mode"].choices)
        self.assertEqual(len(choices), 2)
        self.assertIn(("cash", "Cash"), choices)
        self.assertIn(("upi", "UPI"), choices)

    def test_entry_form_renders_both_cash_and_upi_labels(self):
        """Regression: the entry-form template was reading `radio.choice_value`
        (which doesn't exist on BoundWidget) instead of `radio.data.value`,
        so both Cash and UPI radios rendered with empty labels. This test
        fetches the form HTML and asserts both human labels are present."""
        self.login(self.s1)
        resp = self.client.get(f"/dukaan/{self.retailer.pk}/entry/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Both labels show up exactly where the template renders them.
        self.assertIn("💵 Cash", body)
        self.assertIn("📱 UPI", body)
        # And the value attributes on the radios are right.
        self.assertIn('value="cash"', body)
        self.assertIn('value="upi"', body)

    # `test_entry_new_with_missing_kind_shows_error` was removed in Phase C
    # — the salesman flow no longer has a kind toggle, so a POST without
    # `kind` is the normal Jama path. Equivalent coverage now lives in
    # `EntryNewTests.test_udhar_post_does_not_create_a_sale`.


class AdminDashboardTodayTests(_ViewBase):
    """Phase 3 A1 — admin's Today's Report view (`/dashboard/`)."""

    def test_salesman_bounced_off_dashboard(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_admin_sees_dashboard(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Today's Report")

    def test_dashboard_default_shows_global_totals(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("2500"))
        self.login(self.admin)
        resp = self.client.get("/dashboard/")
        # Global Udhar Diya = 3500
        self.assertContains(resp, "3,500")

    def test_dashboard_salesman_filter_scopes_totals(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("2500"))
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/?salesman={self.s1.pk}")
        # Filtered to s1's slice only
        self.assertContains(resp, "1,000")
        # s2's amount should not be the headline figure
        # (it might appear in the salesman-list dropdown name, etc., so check the headline class instead)
        self.assertNotContains(resp, "3,500")

    def test_dashboard_htmx_returns_partial(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/", HTTP_HX_REQUEST="true")
        body = resp.content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("<nav", body)
        # Main content marker
        self.assertIn("Udhar Diya", body)

    def test_unknown_salesman_id_falls_back_to_all(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/?salesman=99999")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_date_falls_back_to_today(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/?date=not-a-date")
        self.assertEqual(resp.status_code, 200)


class AdminDashboardRetailersTests(_ViewBase):
    """Phase 3 A2 + A3 — admin's Retailers list and detail."""

    def setUp(self):
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("5000"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("3000"))
        Sale.objects.create(salesman=self.s1, retailer=self.other_retailer, amount=Decimal("100"))

    def test_retailers_list_admin_only(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/retailers/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    def test_retailers_list_global_baaki(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/retailers/")
        self.assertEqual(resp.status_code, 200)
        # Both retailers visible; Mobile Shoppy baaki = 5000+3000 = 8000
        self.assertContains(resp, "8,000")

    def test_retailers_list_scoped_to_salesman(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/retailers/?salesman={self.s2.pk}")
        # Only s2's contribution: 3,000 at Mobile Shoppy
        self.assertContains(resp, "3,000")
        # 8,000 (global) shouldn't appear as a Baaki figure
        self.assertNotContains(resp, "8,000")

    def test_retailers_search_filter(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/retailers/?q=Sharma")
        self.assertContains(resp, "Sharma Mobile")
        self.assertNotContains(resp, "Mobile Shoppy")

    def test_retailer_detail_admin_only(self):
        self.login(self.s1)
        resp = self.client.get(f"/dashboard/retailers/{self.retailer.pk}/")
        self.assertEqual(resp.status_code, 302)

    def test_retailer_detail_default_shows_all_salesmens_entries(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/retailers/{self.retailer.pk}/")
        # Both salesmen's amounts visible
        self.assertContains(resp, "5,000")
        self.assertContains(resp, "3,000")

    def test_retailer_detail_scoped_filters_timeline(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/retailers/{self.retailer.pk}/?salesman={self.s2.pk}")
        self.assertContains(resp, "3,000")
        # s1's entry should not appear in the scoped timeline
        # (5,000 is s1's, only s2's 3,000 is in scope)
        # Check the headline Baaki shows 3,000, not 8,000
        self.assertNotContains(resp, "8,000")

    def test_retailer_detail_htmx_returns_partial(self):
        self.login(self.admin)
        resp = self.client.get(
            f"/dashboard/retailers/{self.retailer.pk}/", HTTP_HX_REQUEST="true"
        )
        body = resp.content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("<nav", body)


class AdminDashboardSalesmenTests(_ViewBase):
    """Phase 3 A4 — Salesmen list and per-salesman drill-down."""

    def setUp(self):
        # Pick amounts that don't collide with Tailwind color shades (500/600/700/...).
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("1234"))
        Sale.objects.create(salesman=self.s2, retailer=self.retailer, amount=Decimal("8888"))
        Payment.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("234"), mode=Payment.Mode.CASH)

    def test_salesmen_list_admin_only(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/salesmen/")
        self.assertEqual(resp.status_code, 302)

    def test_salesmen_list_shows_each_salesman(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/salesmen/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.s1.full_name)
        self.assertContains(resp, self.s2.full_name)

    def test_salesmen_list_shows_outstanding_baaki(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/salesmen/")
        # s1 outstanding = 1234 - 234 = 1000. s2 outstanding = 8888.
        # Use intcomma-formatted strings to dodge Tailwind class collisions.
        self.assertContains(resp, "1,000")
        self.assertContains(resp, "8,888")

    def test_salesman_detail_admin_only(self):
        self.login(self.s1)
        resp = self.client.get(f"/dashboard/salesmen/{self.s1.pk}/")
        self.assertEqual(resp.status_code, 302)

    def test_salesman_detail_shows_timeline(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/salesmen/{self.s1.pk}/")
        self.assertEqual(resp.status_code, 200)
        # s1's own entries should appear; s2's 8,888 sale should not be in s1's timeline.
        self.assertContains(resp, "1,234")
        self.assertNotContains(resp, "8,888")

    def test_salesman_detail_404_for_admin_user(self):
        """`/dashboard/salesmen/<admin_pk>/` should 404 — admin is not a salesman."""
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/salesmen/{self.admin.pk}/")
        self.assertEqual(resp.status_code, 404)


class ReportsTests(_ViewBase):
    """Phase 4 — Reports index, Baaki Aging, Daily Closing."""

    def setUp(self):
        # s1 sold ₹5,000 then received ₹2,000 → ₹3,000 outstanding at Mobile Shoppy
        Sale.objects.create(salesman=self.s1, retailer=self.retailer, amount=Decimal("5000"))
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("2000"),
            mode=Payment.Mode.UPI,
        )
        # s2 sold ₹1,500 at Sharma Mobile with no payments
        Sale.objects.create(salesman=self.s2, retailer=self.other_retailer, amount=Decimal("1500"))

    def test_index_admin_only(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/reports/")
        self.assertEqual(resp.status_code, 302)

    def test_index_renders_cards(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Baaki Aging")
        self.assertContains(resp, "Daily Closing")

    def test_baaki_aging_buckets_outstanding_retailers(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/baaki-aging/")
        # Both retailers have Baaki > 0 today, so both land in 0-7d bucket.
        self.assertContains(resp, "Mobile Shoppy")
        self.assertContains(resp, "Sharma Mobile")
        # Grand total = 3,000 + 1,500 = 4,500
        self.assertContains(resp, "4,500")

    def test_baaki_aging_scoped_to_salesman(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/reports/baaki-aging/?salesman={self.s2.pk}")
        # Only s2's outstanding (1,500 at Sharma)
        self.assertContains(resp, "Sharma Mobile")
        self.assertNotContains(resp, "Mobile Shoppy")

    def test_baaki_aging_fifo_handles_full_settlement(self):
        """A retailer whose payments fully cover all sales should not appear
        in the aging report, even if individual sales exist."""
        # Pay off the remaining ₹3,000 at Mobile Shoppy
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("3000"),
            mode=Payment.Mode.CASH,
        )
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/baaki-aging/")
        # Mobile Shoppy is fully settled (Baaki=0), should not appear; Sharma still owes 1,500.
        self.assertNotContains(resp, "Mobile Shoppy")
        self.assertContains(resp, "Sharma Mobile")

    def test_daily_closing_today(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/daily-closing/")
        self.assertEqual(resp.status_code, 200)
        # Total Udhar today = 5,000 + 1,500 = 6,500
        self.assertContains(resp, "6,500")
        # Total Jama today = 2,000
        self.assertContains(resp, "2,000")
        # Net Baaki Change = 6,500 - 2,000 = 4,500
        self.assertContains(resp, "4,500")

    def test_daily_closing_scoped_to_salesman(self):
        self.login(self.admin)
        resp = self.client.get(f"/dashboard/reports/daily-closing/?salesman={self.s2.pk}")
        # Only s2's ₹1,500 sale appears
        self.assertContains(resp, "1,500")
        # s1's 5,000 sale should be filtered out of the headlines
        # (could still appear in salesman-list option text — check for the row context)
        # Use a sufficiently specific Tailwind-safe string:
        self.assertNotContains(resp, "6,500")

    def test_daily_closing_admin_only(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/reports/daily-closing/")
        self.assertEqual(resp.status_code, 302)

    def test_aging_htmx_returns_partial(self):
        self.login(self.admin)
        resp = self.client.get(
            "/dashboard/reports/baaki-aging/", HTTP_HX_REQUEST="true"
        )
        body = resp.content.decode()
        self.assertNotIn("<html", body)
        self.assertIn("Grand Total Outstanding", body)

    # --- Phase 4 part 2 ---

    def test_salesman_performance_admin_only(self):
        self.login(self.s1)
        resp = self.client.get("/dashboard/reports/salesman-performance/")
        self.assertEqual(resp.status_code, 302)

    def test_salesman_performance_renders(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/salesman-performance/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Salesman Performance")
        # Both salesmen appear in the table.
        self.assertContains(resp, self.s1.full_name)
        self.assertContains(resp, self.s2.full_name)

    def test_retailer_statement_picker(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/retailer-statement/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "pick one")

    def test_retailer_statement_renders_for_retailer(self):
        from datetime import date
        self.login(self.admin)
        resp = self.client.get(
            f"/dashboard/reports/retailer-statement/?retailer={self.retailer.pk}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Closing Baaki")
        self.assertContains(resp, "Running Baaki")
        # Running baaki ends at 3,000 (5,000 sold - 2,000 paid).
        self.assertContains(resp, "3,000")

    # --- CSV exports ---

    def test_daily_closing_csv(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/daily-closing/?format=csv")
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", resp["Content-Disposition"])
        body = resp.content.decode("utf-8-sig")
        self.assertIn("when,type,retailer,salesman,amount,notes", body)
        self.assertIn("Mobile Shoppy", body)

    def test_baaki_aging_csv(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/baaki-aging/?format=csv")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8-sig")
        self.assertIn("bucket,retailer,area,baaki,age_days", body)

    def test_salesman_performance_csv(self):
        self.login(self.admin)
        resp = self.client.get("/dashboard/reports/salesman-performance/?format=csv")
        body = resp.content.decode("utf-8-sig")
        self.assertIn("salesman,username,entries", body)

    def test_retailer_statement_csv(self):
        self.login(self.admin)
        resp = self.client.get(
            f"/dashboard/reports/retailer-statement/?retailer={self.retailer.pk}&format=csv"
        )
        body = resp.content.decode("utf-8-sig")
        self.assertIn("when,type,amount,salesman,notes,running_baaki", body)

    def test_aging_overpayment_carries_forward(self):
        """Reviewer Watch on `2d376fc`: an overpayment should be applied
        to the next incoming sale rather than discarded.

        Sequence: ₹100 sale, ₹150 payment (₹50 overpayment), ₹200 sale.
        - Baaki = 100 + 200 − 150 = ₹150 (covered by `with_baaki`).
        - FIFO must apply the ₹50 credit to the ₹200 sale, leaving
          remaining=₹150 for that second sale. Without the carry-forward
          the queue would hold ₹200, divergent from the displayed Baaki.
        """
        from .reports import _oldest_unsettled_sale

        # Wipe existing fixtures on this retailer for a clean run.
        Sale.objects.filter(retailer=self.retailer).delete()
        Payment.objects.filter(retailer=self.retailer).delete()

        now = timezone.now()
        Sale.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("100"),
            occurred_at=now - timedelta(days=30),
        )
        Payment.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("150"),
            mode=Payment.Mode.UPI, occurred_at=now - timedelta(days=20),
        )
        Sale.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("200"),
            occurred_at=now - timedelta(days=10),
        )

        result = _oldest_unsettled_sale(self.retailer, None, now)
        self.assertIsNotNone(result)
        # Oldest unsettled = the ₹200 sale (the ₹100 was fully covered
        # plus a ₹50 credit which was applied here, leaving ₹150 remaining).
        self.assertEqual(result["remaining"], Decimal("150"))
        # Its age = 10 days.
        self.assertEqual(result["age_days"], 10)

    def test_aging_future_dated_sale_clamps_to_zero(self):
        """Future-dated sales (admin backdate gone wrong) clamp to age 0
        instead of falling through to the 60+ default bucket."""
        from .reports import _oldest_unsettled_sale

        Sale.objects.filter(retailer=self.retailer).delete()
        Payment.objects.filter(retailer=self.retailer).delete()

        now = timezone.now()
        Sale.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("100"),
            occurred_at=now + timedelta(days=5),  # future-dated
        )
        result = _oldest_unsettled_sale(self.retailer, None, now)
        self.assertIsNotNone(result)
        self.assertEqual(result["age_days"], 0)


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
    """Phase C: salesmen can only edit/delete their own Payments (Jama).
    Sales (Udhar) come from the Jio import and are read-only on the
    salesman side — admin edits them via Django Admin if a correction is
    needed."""

    def setUp(self):
        self.payment = Payment.objects.create(
            salesman=self.s1, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
        )
        # Keep a Sale in the fixture so we can assert it's untouchable from
        # the salesman side.
        self.sale = Sale.objects.create(
            salesman=self.s1, retailer=self.retailer, amount=Decimal("1000"),
        )

    def test_edit_jama_within_24h_works(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/jama/{self.payment.pk}/edit/",
            {"amount": "750", "mode": "cash", "notes": "edited",
             "reason": "Typo fix"},
        )
        self.assertEqual(resp.status_code, 302)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal("750"))

    def test_edit_other_salesmans_jama_returns_404(self):
        self.login(self.s2)
        resp = self.client.post(
            f"/entry/jama/{self.payment.pk}/edit/",
            {"amount": "750", "mode": "cash"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_edit_jama_after_24h_forbidden(self):
        Payment.objects.filter(pk=self.payment.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/jama/{self.payment.pk}/edit/",
            {"amount": "750", "mode": "cash"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_jama_requires_reason(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/jama/{self.payment.pk}/delete/",
            {"reason": "   "},
        )
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.is_deleted)

    def test_delete_jama_with_reason_soft_deletes(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/jama/{self.payment.pk}/delete/",
            {"reason": "Wrong amount"},
        )
        self.assertEqual(resp.status_code, 302)
        self.payment.refresh_from_db()
        self.assertTrue(self.payment.is_deleted)
        self.assertEqual(self.payment.deleted_reason, "Wrong amount")

    def test_edit_udhar_forbidden_on_salesman_path(self):
        """Hitting /entry/udhar/<pk>/edit/ with a salesman session returns
        403 — Sales aren't salesman-editable in the new flow."""
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/edit/",
            {"amount": "750"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_udhar_forbidden_on_salesman_path(self):
        self.login(self.s1)
        resp = self.client.post(
            f"/entry/udhar/{self.sale.pk}/delete/",
            {"reason": "Trying"},
        )
        self.assertEqual(resp.status_code, 403)


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

    def test_snapshot_canonicalizes_decimal_to_two_places(self):
        # Reviewer Watch on 317ca6e: before/after snapshots compared
        # "400.00" vs "600", false-positive noise in audit diffs.
        # Now: both sides canonicalize Decimals to two-place strings.
        from .audit import _coerce
        self.assertEqual(_coerce(Decimal("600")), "600.00")
        self.assertEqual(_coerce(Decimal("400.00")), "400.00")
        self.assertEqual(_coerce(Decimal("1234.5")), "1234.50")

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


class PhoneNormalizationTests(TestCase):
    """Phone numbers must land in canonical +91XXXXXXXXXX before storage."""

    def test_bare_ten_digits_normalized(self):
        from .phones import normalize_indian_phone
        self.assertEqual(normalize_indian_phone("9876543210"), "+919876543210")

    def test_strip_spaces_hyphens_parens(self):
        from .phones import normalize_indian_phone
        self.assertEqual(
            normalize_indian_phone("(98765) 43-210"), "+919876543210"
        )

    def test_already_canonical(self):
        from .phones import normalize_indian_phone
        self.assertEqual(
            normalize_indian_phone("+919876543210"), "+919876543210"
        )

    def test_strip_leading_country_code(self):
        from .phones import normalize_indian_phone
        self.assertEqual(
            normalize_indian_phone("91 98765 43210"), "+919876543210"
        )

    def test_strip_leading_national_zero(self):
        from .phones import normalize_indian_phone
        self.assertEqual(
            normalize_indian_phone("09876543210"), "+919876543210"
        )

    def test_blank_returns_blank(self):
        from .phones import normalize_indian_phone
        self.assertEqual(normalize_indian_phone(""), "")
        self.assertEqual(normalize_indian_phone(None), "")

    def test_too_short_raises(self):
        from .phones import normalize_indian_phone
        with self.assertRaises(ValidationError):
            normalize_indian_phone("12345")

    def test_invalid_first_digit_raises(self):
        # Indian mobiles start with 6/7/8/9. Landlines (start with 0 then
        # area code, or 2-5 directly) aren't mobiles.
        from .phones import normalize_indian_phone
        with self.assertRaises(ValidationError):
            normalize_indian_phone("1234567890")
        with self.assertRaises(ValidationError):
            normalize_indian_phone("5876543210")

    def test_retailer_save_canonicalizes(self):
        r = Retailer.objects.create(name="Dukaan A", phone="9876543210")
        r.refresh_from_db()
        self.assertEqual(r.phone, "+919876543210")

    def test_retailer_save_with_blank_phone(self):
        r = Retailer.objects.create(name="Dukaan B")
        self.assertEqual(r.phone, "")

    def test_retailer_save_rejects_bad_phone(self):
        with self.assertRaises(ValidationError):
            Retailer.objects.create(name="Dukaan C", phone="not-a-number")

    def test_retailer_clean_surfaces_field_error_for_admin_form(self):
        # The admin form path goes through full_clean() → clean(). A bad
        # phone must surface as a field error, not raise out of save() and
        # bubble into a 500 page.
        r = Retailer(name="Dukaan D", phone="not-a-number")
        with self.assertRaises(ValidationError) as ctx:
            r.full_clean()
        self.assertIn("phone", ctx.exception.error_dict)

    def test_admin_add_retailer_with_bad_phone_returns_form_error(self):
        # End-to-end: the Django Admin add view must re-render the form
        # with a field-level error, not 500.
        admin = User.objects.create_user(
            username="phone-adm", password="x", full_name="Phone Owner",
            role=User.Role.ADMIN, is_staff=True, is_superuser=True,
        )
        self.client.force_login(admin)
        resp = self.client.post(
            "/admin/core/retailer/add/",
            data={"name": "Bad Phone Dukaan", "phone": "not-a-number"},
            follow=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no digits", status_code=200)
        self.assertFalse(
            Retailer.objects.filter(name="Bad Phone Dukaan").exists()
        )


class NotificationMessageTests(TestCase):
    """The body text retailers see for each Notification kind."""

    @classmethod
    def setUpTestData(cls):
        cls.salesman = _fresh_user(username="msg-sales")
        cls.salesman.full_name = "Ramesh Kumar"
        cls.salesman.save()
        cls.retailer = _fresh_retailer(name="Sharma Mobile")

    def _payment(self, amount="500", mode=Payment.Mode.CASH):
        return Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal(amount), mode=mode,
        )

    def test_received_body_has_amount_mode_and_names(self):
        from .notifications import build_body
        body = build_body(kind="received", payment=self._payment())
        self.assertIn("Sharma Mobile", body)
        self.assertIn("Ramesh Kumar", body)
        self.assertIn("₹500", body)
        self.assertIn("Cash", body)

    def test_updated_body_shows_before_and_after(self):
        from .notifications import build_body
        p = self._payment(amount="1000", mode=Payment.Mode.UPI)
        body = build_body(
            kind="updated", payment=p, previous_amount=Decimal("500"),
        )
        self.assertIn("Pehle: ₹500", body)
        self.assertIn("Ab: ₹1,000", body)
        self.assertIn("UPI", body)

    def test_cancelled_body_uses_current_amount(self):
        from .notifications import build_body
        body = build_body(kind="cancelled", payment=self._payment("250"))
        self.assertIn("₹250", body)
        self.assertIn("cancel", body.lower())

    def test_unknown_kind_raises(self):
        from .notifications import build_body
        with self.assertRaises(ValueError):
            build_body(kind="bogus", payment=self._payment())

    def test_updated_without_previous_amount_raises(self):
        # Watch on 9721285: silent fallback rendered "Pehle ₹X / Ab ₹X" —
        # worse than no message. Now raises so callers get a clear error.
        from .notifications import build_body
        with self.assertRaises(ValueError):
            build_body(kind="updated", payment=self._payment(), previous_amount=None)


class NotificationProviderFactoryTests(TestCase):
    """Factory picks the right class per settings.NOTIFICATION_PROVIDER."""

    def setUp(self):
        from .notifications.factory import reset_cache
        reset_cache()
        self.addCleanup(reset_cache)

    def test_console_provider_default(self):
        from django.test import override_settings
        from .notifications import get_provider
        from .notifications.console import ConsoleProvider
        with override_settings(NOTIFICATION_PROVIDER="console"):
            self.assertIsInstance(get_provider(), ConsoleProvider)

    def test_telegram_provider_requires_token(self):
        from django.test import override_settings
        from .notifications import get_provider
        with override_settings(
            NOTIFICATION_PROVIDER="telegram", TELEGRAM_BOT_TOKEN=""
        ):
            with self.assertRaises(RuntimeError):
                get_provider()

    def test_telegram_provider_built_with_token(self):
        from django.test import override_settings
        from .notifications import get_provider
        from .notifications.telegram import TelegramProvider
        with override_settings(
            NOTIFICATION_PROVIDER="telegram",
            TELEGRAM_BOT_TOKEN="t0ken",
            TELEGRAM_API_BASE="https://api.telegram.org",
        ):
            p = get_provider()
            self.assertIsInstance(p, TelegramProvider)
            self.assertEqual(p.token, "t0ken")

    def test_unknown_provider_raises(self):
        from django.test import override_settings
        from .notifications import get_provider
        with override_settings(NOTIFICATION_PROVIDER="carrier-pigeon"):
            with self.assertRaises(RuntimeError):
                get_provider()

    def test_console_address_for_prefers_chat_id(self):
        # Mirrors TelegramProvider in dev: prefer telegram_chat_id so the
        # body logged in dev matches what prod will see. Phone is the
        # fallback for retailers that don't have a chat_id yet.
        from .notifications.console import ConsoleProvider
        p = ConsoleProvider()
        with_both = Retailer(phone="+919876543210", telegram_chat_id="123")
        self.assertEqual(p.address_for(with_both), "123")
        phone_only = Retailer(phone="+919876543210", telegram_chat_id="")
        self.assertEqual(p.address_for(phone_only), "+919876543210")
        neither = Retailer(phone="", telegram_chat_id="")
        self.assertEqual(p.address_for(neither), "")

    def test_console_provider_channel_is_console(self):
        # Not "telegram" — fixes the lie that would feed phones-as-chat-ids
        # to TelegramProvider after a flip from console to telegram.
        from .notifications.console import ConsoleProvider
        self.assertEqual(ConsoleProvider().channel, "console")

    def test_telegram_address_for_uses_chat_id(self):
        from .notifications.telegram import TelegramProvider
        r = Retailer(phone="+919876543210", telegram_chat_id="456")
        p = TelegramProvider(
            token="x", api_base="https://api.telegram.org", timeout=5,
        )
        self.assertEqual(p.address_for(r), "456")


class _FailingProvider:
    """Test double — always returns FAILED."""

    channel = "telegram"

    def address_for(self, retailer):
        return retailer.phone

    def send(self, *, address, body):
        from .notifications import SendOutcome, SendResult
        return SendResult(outcome=SendOutcome.FAILED, error="boom")


class NotificationDispatcherTests(TestCase):
    """The retry chain: each attempt is its own row; failed rows enqueue
    the next try; the schedule's last try ends with ABANDONED."""

    @classmethod
    def setUpTestData(cls):
        cls.salesman = _fresh_user(username="disp-sales")
        cls.retailer = Retailer.objects.create(
            name="Disp Dukaan", phone="9876543210",
            assigned_salesman=cls.salesman,
        )

    def _make_queued(self):
        from .models import Notification
        from .notifications import build_body
        payment = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
        )
        return Notification.objects.create(
            payment=payment,
            kind=Notification.Kind.RECEIVED,
            channel="telegram",
            address=self.retailer.phone,
            body=build_body(kind="received", payment=payment),
            status=Notification.Status.QUEUED,
            attempt_number=1,
            send_after=timezone.now(),
        )

    def _run_dispatcher(self):
        from django.core.management import call_command
        call_command("dispatch_notifications", verbosity=0)

    def setUp(self):
        from .notifications.factory import reset_cache
        reset_cache()
        self.addCleanup(reset_cache)

    def test_dispatcher_marks_sent_on_success(self):
        from .models import Notification
        n = self._make_queued()
        with self.settings(NOTIFICATION_PROVIDER="console"):
            self._run_dispatcher()
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.Status.SENT)
        self.assertEqual(n.provider_message_id, "console")
        self.assertIsNotNone(n.attempted_at)

    def test_dispatcher_failed_enqueues_next_attempt(self):
        from unittest.mock import patch
        from .models import Notification
        n = self._make_queued()
        # Patch get_provider where the dispatcher imported it.
        with patch(
            "core.management.commands.dispatch_notifications.get_provider",
            return_value=_FailingProvider(),
        ):
            self._run_dispatcher()
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.Status.FAILED)
        self.assertIn("boom", n.error)
        retries = Notification.objects.filter(previous_attempt=n)
        self.assertEqual(retries.count(), 1)
        retry = retries.first()
        self.assertEqual(retry.status, Notification.Status.QUEUED)
        self.assertEqual(retry.attempt_number, 2)
        self.assertGreater(retry.send_after, timezone.now())

    def test_dispatcher_skips_rows_with_future_send_after(self):
        from .models import Notification
        n = self._make_queued()
        n.send_after = timezone.now() + timedelta(minutes=5)
        n.save(update_fields=["send_after"])
        with self.settings(NOTIFICATION_PROVIDER="console"):
            self._run_dispatcher()
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.Status.QUEUED)

    def test_dispatcher_abandons_after_schedule_exhausted(self):
        from unittest.mock import patch
        from django.conf import settings as dj_settings
        from .models import Notification
        n = self._make_queued()
        n.attempt_number = len(dj_settings.NOTIFICATION_RETRY_BACKOFF_SECONDS) + 1
        n.save(update_fields=["attempt_number"])
        with patch(
            "core.management.commands.dispatch_notifications.get_provider",
            return_value=_FailingProvider(),
        ):
            self._run_dispatcher()
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.Status.FAILED)
        chain_end = Notification.objects.filter(previous_attempt=n).first()
        self.assertIsNotNone(chain_end)
        self.assertEqual(chain_end.status, Notification.Status.ABANDONED)


class NotificationEnqueueTests(TestCase):
    """View-layer hooks must enqueue a Notification on Payment lifecycle."""

    @classmethod
    def setUpTestData(cls):
        cls.salesman = User.objects.create_user(
            username="hk-s1", password="x", full_name="Hook One",
            role=User.Role.SALESMAN,
        )
        cls.retailer = Retailer.objects.create(
            name="Hook Dukaan", phone="9876543210",
            assigned_salesman=cls.salesman,
        )

    def setUp(self):
        from .notifications.factory import reset_cache
        reset_cache()
        self.client.force_login(self.salesman)

    def test_entry_new_enqueues_received(self):
        from .models import Notification
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            data={"amount": "500", "mode": Payment.Mode.CASH, "notes": ""},
        )
        self.assertEqual(resp.status_code, 302)
        notif = Notification.objects.get()
        self.assertEqual(notif.kind, Notification.Kind.RECEIVED)
        self.assertEqual(notif.status, Notification.Status.QUEUED)
        self.assertIn("500", notif.body)

    def test_entry_edit_enqueues_updated_on_amount_change(self):
        from .models import Notification
        p = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
        )
        resp = self.client.post(
            f"/entry/jama/{p.pk}/edit/",
            data={"amount": "750", "mode": Payment.Mode.CASH, "notes": "",
                  "reason": "miscounted"},
        )
        self.assertEqual(resp.status_code, 302)
        notifs = list(Notification.objects.filter(payment=p))
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].kind, Notification.Kind.UPDATED)
        self.assertIn("Pehle: ₹500", notifs[0].body)
        self.assertIn("Ab: ₹750", notifs[0].body)

    def test_entry_edit_no_material_change_no_enqueue(self):
        from .models import Notification
        p = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
            notes="original",
        )
        resp = self.client.post(
            f"/entry/jama/{p.pk}/edit/",
            data={"amount": "500", "mode": Payment.Mode.CASH,
                  "notes": "fixed typo", "reason": "typo in notes"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Notification.objects.filter(payment=p).count(), 0)

    def test_entry_delete_enqueues_cancelled(self):
        from .models import Notification
        p = Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal("500"), mode=Payment.Mode.CASH,
        )
        resp = self.client.post(
            f"/entry/jama/{p.pk}/delete/",
            data={"reason": "Wrong dukaan"},
        )
        self.assertEqual(resp.status_code, 302)
        notif = Notification.objects.get(payment=p)
        self.assertEqual(notif.kind, Notification.Kind.CANCELLED)

    def test_no_address_no_enqueue(self):
        from .models import Notification
        r = Retailer.objects.create(
            name="No Phone Dukaan", assigned_salesman=self.salesman,
        )
        self.assertEqual(r.phone, "")
        resp = self.client.post(
            f"/dukaan/{r.pk}/entry/",
            data={"amount": "100", "mode": Payment.Mode.CASH, "notes": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Notification.objects.count(), 0)


class AuditLogReasonTests(TestCase):
    """Edit and delete forms must capture WHY into AuditLog.reason.

    Fraud-prevention requirement (PLAN §1 + owner ask): the audit trail
    has to record both before/after AND a human-typed reason.
    """

    @classmethod
    def setUpTestData(cls):
        cls.salesman = User.objects.create_user(
            username="rsn-s1", password="x", full_name="Reason One",
            role=User.Role.SALESMAN,
        )
        cls.retailer = Retailer.objects.create(
            name="Reason Dukaan", phone="9876543210",
            assigned_salesman=cls.salesman,
        )

    def setUp(self):
        from .notifications.factory import reset_cache
        reset_cache()
        self.client.force_login(self.salesman)

    def _new_payment(self, amount="500"):
        return Payment.objects.create(
            salesman=self.salesman, retailer=self.retailer,
            amount=Decimal(amount), mode=Payment.Mode.CASH,
        )

    def test_edit_without_reason_returns_form_error(self):
        from .models import AuditLog
        p = self._new_payment()
        resp = self.client.post(
            f"/entry/jama/{p.pk}/edit/",
            data={"amount": "600", "mode": Payment.Mode.CASH, "notes": ""},
        )
        # Form invalid → re-render, not redirect.
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit ka reason zaroori hai.")
        p.refresh_from_db()
        # Amount must not change without a reason.
        self.assertEqual(p.amount, Decimal("500.00"))
        # And no UPDATE AuditLog entry must have been written.
        updates = AuditLog.objects.filter(
            entity_type="Payment", entity_id=p.pk,
            action=AuditLog.Action.UPDATE,
        )
        self.assertEqual(updates.count(), 0)

    def test_edit_with_reason_writes_reason_to_audit(self):
        from .models import AuditLog
        p = self._new_payment()
        resp = self.client.post(
            f"/entry/jama/{p.pk}/edit/",
            data={
                "amount": "600", "mode": Payment.Mode.CASH, "notes": "",
                "reason": "Retailer ne 100 extra diya tha",
            },
        )
        self.assertEqual(resp.status_code, 302)
        audit = AuditLog.objects.get(
            entity_type="Payment", entity_id=p.pk,
            action=AuditLog.Action.UPDATE,
        )
        self.assertEqual(audit.reason, "Retailer ne 100 extra diya tha")
        # And the before snapshot is the *pre-edit* state.
        self.assertEqual(str(audit.before["amount"]), "500.00")

    def test_delete_writes_reason_to_audit(self):
        from .models import AuditLog
        p = self._new_payment()
        resp = self.client.post(
            f"/entry/jama/{p.pk}/delete/",
            data={"reason": "Wrong dukaan select kar diya"},
        )
        self.assertEqual(resp.status_code, 302)
        audit = AuditLog.objects.get(
            entity_type="Payment", entity_id=p.pk,
            action=AuditLog.Action.DELETE,
        )
        self.assertEqual(audit.reason, "Wrong dukaan select kar diya")

    def test_create_does_not_require_reason(self):
        # The create itself IS the why. Reason is only for edits/deletes.
        from .models import AuditLog
        resp = self.client.post(
            f"/dukaan/{self.retailer.pk}/entry/",
            data={"amount": "750", "mode": Payment.Mode.CASH, "notes": ""},
        )
        self.assertEqual(resp.status_code, 302)
        audit = AuditLog.objects.get(action=AuditLog.Action.CREATE)
        self.assertEqual(audit.reason, "")
