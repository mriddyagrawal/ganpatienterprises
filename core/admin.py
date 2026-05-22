from django.contrib import admin
from unfold.admin import ModelAdmin

from .audit import log_change, snapshot
from .models import AuditLog, Payment, Retailer, Sale, Visit


class AuditedModelAdmin(ModelAdmin):
    """ModelAdmin subclass that writes AuditLog rows on every save / delete."""

    def save_model(self, request, obj, form, change):
        before = snapshot(obj) if change and obj.pk else None
        super().save_model(request, obj, form, change)
        log_change(
            actor=request.user,
            instance=obj,
            action=AuditLog.Action.UPDATE if change else AuditLog.Action.CREATE,
            before=before,
        )

    def delete_model(self, request, obj):
        before = snapshot(obj)
        log_change(
            actor=request.user,
            instance=obj,
            action=AuditLog.Action.DELETE,
            before=before,
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # Audit each deletion individually so the log captures who and what.
        for obj in queryset:
            self.delete_model(request, obj)


# ---------------------------------------------------------------------------
# Retailer
# ---------------------------------------------------------------------------


@admin.register(Retailer)
class RetailerAdmin(ModelAdmin):
    list_display = ("name", "area", "phone", "owner_name", "baaki_display", "is_active")
    list_filter = ("is_active", "area")
    search_fields = ("name", "owner_name", "phone", "area")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "owner_name", "phone")}),
        ("Location", {"fields": ("area", "address")}),
        ("Internal", {"fields": ("notes", "is_active", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).with_baaki()

    @admin.display(description="Baaki (₹)", ordering="baaki")
    def baaki_display(self, obj):
        return obj.baaki


# ---------------------------------------------------------------------------
# Visit
# ---------------------------------------------------------------------------


@admin.register(Visit)
class VisitAdmin(ModelAdmin):
    list_display = ("retailer", "salesman", "started_at", "last_activity_at")
    list_filter = ("salesman", "retailer")
    search_fields = ("retailer__name", "salesman__username", "salesman__full_name")
    date_hierarchy = "last_activity_at"
    readonly_fields = ("started_at", "last_activity_at", "created_at", "updated_at")


# ---------------------------------------------------------------------------
# Sale (Udhar)
# ---------------------------------------------------------------------------


@admin.register(Sale)
class SaleAdmin(AuditedModelAdmin):
    list_display = ("retailer", "amount", "salesman", "occurred_at", "is_deleted")
    list_filter = ("is_deleted", "salesman", "occurred_at")
    search_fields = ("retailer__name", "salesman__username", "salesman__full_name", "notes")
    date_hierarchy = "occurred_at"
    autocomplete_fields = ("retailer", "salesman")
    readonly_fields = ("visit", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("retailer", "salesman", "amount", "occurred_at", "notes")}),
        ("Deletion", {"fields": ("is_deleted", "deleted_reason"), "classes": ("collapse",)}),
        ("Internal", {"fields": ("visit", "created_at", "updated_at"), "classes": ("collapse",)}),
    )


# ---------------------------------------------------------------------------
# Payment (Jama)
# ---------------------------------------------------------------------------


@admin.register(Payment)
class PaymentAdmin(AuditedModelAdmin):
    list_display = ("retailer", "amount", "mode", "salesman", "occurred_at", "is_deleted")
    list_filter = ("mode", "is_deleted", "salesman", "occurred_at")
    search_fields = ("retailer__name", "salesman__username", "salesman__full_name", "notes")
    date_hierarchy = "occurred_at"
    autocomplete_fields = ("retailer", "salesman")
    readonly_fields = ("visit", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("retailer", "salesman", "amount", "mode", "occurred_at", "notes")}),
        ("Deletion", {"fields": ("is_deleted", "deleted_reason"), "classes": ("collapse",)}),
        ("Internal", {"fields": ("visit", "created_at", "updated_at"), "classes": ("collapse",)}),
    )


# ---------------------------------------------------------------------------
# AuditLog (read-only)
# ---------------------------------------------------------------------------


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = ("at", "actor", "action", "entity_type", "entity_id")
    list_filter = ("action", "entity_type", "actor")
    search_fields = ("entity_type", "entity_id")
    date_hierarchy = "at"
    readonly_fields = ("actor", "entity_type", "entity_id", "action", "before", "after", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
