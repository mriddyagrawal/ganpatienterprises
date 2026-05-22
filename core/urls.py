from django.urls import path

from . import dashboard, views

app_name = "core"

urlpatterns = [
    # Salesman flow (Phase 2)
    path("", views.dukaan, name="dukaan"),
    path("dukaan/<int:pk>/", views.retailer_detail, name="retailer_detail"),
    path("dukaan/<int:pk>/entry/", views.entry_new, name="entry_new"),
    path("entry/new/", views.entry_new_picker, name="entry_new_picker"),
    path("entry/<str:kind>/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("entry/<str:kind>/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("aaj/", views.aaj, name="aaj"),

    # Admin dashboard (Phase 3)
    path("dashboard/", dashboard.today, name="dashboard_today"),
    path("dashboard/retailers/", dashboard.retailers, name="dashboard_retailers"),
    path("dashboard/retailers/<int:pk>/", dashboard.retailer_detail, name="dashboard_retailer_detail"),
]
