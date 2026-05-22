from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dukaan, name="dukaan"),
    path("dukaan/<int:pk>/", views.retailer_detail, name="retailer_detail"),
    path("dukaan/<int:pk>/entry/", views.entry_new, name="entry_new"),
    path("entry/new/", views.entry_new_picker, name="entry_new_picker"),
    path("entry/<str:kind>/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("entry/<str:kind>/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("aaj/", views.aaj, name="aaj"),
]
