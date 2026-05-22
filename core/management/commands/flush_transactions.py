"""
Delete every Sale, Payment, Visit, and AuditLog row.

Users (admin + salesmen) and Retailers (including their
`assigned_salesman` and `jio_partner_id`) are preserved. This is the
"wipe the books, keep the cast" command — useful while testing
imports / iterating on the data model. Never run this on real
production data without a backup.

Usage:
    uv run python manage.py flush_transactions          # interactive confirm
    uv run python manage.py flush_transactions --yes    # skip confirm
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import AuditLog, Payment, Sale, Visit


class Command(BaseCommand):
    help = (
        "Delete all transactions (Sales, Payments, Visits, AuditLogs). "
        "Keeps Users and Retailers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive 'type DELETE to confirm' prompt.",
        )

    def handle(self, *args, **opts):
        counts = {
            "Sales": Sale.objects.count(),
            "Payments": Payment.objects.count(),
            "Visits": Visit.objects.count(),
            "AuditLogs": AuditLog.objects.count(),
        }
        total = sum(counts.values())

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to delete — every transaction table is already empty."))
            return

        self.stdout.write("About to delete:")
        for k, v in counts.items():
            self.stdout.write(f"  {k:<10s} {v:>6d}")
        self.stdout.write(f"  {'TOTAL':<10s} {total:>6d}")
        self.stdout.write("")
        self.stdout.write("Users and Retailers will NOT be touched.")

        if not opts["yes"]:
            self.stdout.write("")
            answer = input("Type DELETE (uppercase) to confirm: ")
            if answer != "DELETE":
                self.stdout.write(self.style.WARNING("Aborted — nothing was changed."))
                return

        with transaction.atomic():
            # Order matters: Sale.visit and Payment.visit use on_delete=PROTECT,
            # so we delete the children before their Visit parent.
            sales_deleted, _ = Sale.objects.all().delete()
            payments_deleted, _ = Payment.objects.all().delete()
            visits_deleted, _ = Visit.objects.all().delete()
            audit_deleted, _ = AuditLog.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f"Deleted: {sales_deleted} Sales, {payments_deleted} Payments, "
            f"{visits_deleted} Visits, {audit_deleted} AuditLogs."
        ))
