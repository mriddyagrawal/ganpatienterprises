from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        SALESMAN = "salesman", "Salesman"

    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.SALESMAN,
    )
    full_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)

    # Jio FOS (Field Operations Salesman) identifier — appears on every
    # row of Jio's auto-refill report. The importer matches by this to
    # attribute each Sale to the correct salesman. Stored as CharField
    # to preserve leading zeros (e.g. "0691060960").
    jio_fos_id = models.CharField(
        max_length=32, unique=True, null=True, blank=True,
        help_text="Jio FOS ID from the auto-refill report. Matches rows during import.",
    )

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN

    @property
    def is_salesman_role(self) -> bool:
        return self.role == self.Role.SALESMAN

    def __str__(self) -> str:
        return self.full_name or self.username
