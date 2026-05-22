"""
Seed a fresh dev database with the minimum data needed to smoke-test the app:
one admin (superuser), one salesman, three retailers.

Idempotent — re-running the command is safe; existing rows are left alone.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Retailer

User = get_user_model()


ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"  # dev-only; never deploy with this

SALESMAN_USERNAME = "salesman1"
SALESMAN_PASSWORD = "salesman123"  # dev-only

RETAILERS = [
    {"name": "Mobile Shoppy", "owner_name": "Rajesh Sharma", "phone": "9876500001", "area": "Bhagat Singh Market"},
    {"name": "Sharma Mobile", "owner_name": "Vikas Sharma", "phone": "9876500002", "area": "Bhagat Singh Market"},
    {"name": "Krishna Electronics", "owner_name": "Krishna Kumar", "phone": "9876500003", "area": "Civil Lines"},
]


class Command(BaseCommand):
    help = "Seed dev data: one admin user, one salesman, three retailers."

    def handle(self, *args, **options):
        admin, created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={
                "full_name": "Owner Admin",
                "role": User.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            admin.set_password(ADMIN_PASSWORD)
            admin.save()
            self.stdout.write(self.style.SUCCESS(f"Created admin '{ADMIN_USERNAME}' (password: {ADMIN_PASSWORD})"))
        else:
            self.stdout.write(f"Admin '{ADMIN_USERNAME}' already exists; left untouched.")

        salesman, created = User.objects.get_or_create(
            username=SALESMAN_USERNAME,
            defaults={
                "full_name": "Ramesh Kumar",
                "phone": "9876511111",
                "role": User.Role.SALESMAN,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if created:
            salesman.set_password(SALESMAN_PASSWORD)
            salesman.save()
            self.stdout.write(self.style.SUCCESS(f"Created salesman '{SALESMAN_USERNAME}' (password: {SALESMAN_PASSWORD})"))
        else:
            self.stdout.write(f"Salesman '{SALESMAN_USERNAME}' already exists; left untouched.")

        for spec in RETAILERS:
            retailer, created = Retailer.objects.get_or_create(name=spec["name"], defaults=spec)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created retailer '{retailer.name}'"))
            else:
                self.stdout.write(f"Retailer '{retailer.name}' already exists; left untouched.")

        self.stdout.write(self.style.SUCCESS("Seed complete."))
