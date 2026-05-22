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

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN

    @property
    def is_salesman_role(self) -> bool:
        return self.role == self.Role.SALESMAN

    def __str__(self) -> str:
        return self.full_name or self.username
