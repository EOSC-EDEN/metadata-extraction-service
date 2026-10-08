from django.urls import path

from apps.ui import views

app_name = "ui"

urlpatterns = [
    path("", views.index, name="index"),
    # Same path the FFIS page uses for its favicon/logo.
    path("logo-eden.svg", views.logo, name="logo"),
]
