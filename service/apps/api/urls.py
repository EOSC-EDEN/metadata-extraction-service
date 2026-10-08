from django.urls import path

from apps.api.views import ExtractMetadataView

app_name = "api"

urlpatterns = [
    path("extract/", ExtractMetadataView.as_view(), name="extract"),
]
