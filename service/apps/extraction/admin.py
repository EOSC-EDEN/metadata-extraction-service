from django.contrib import admin

from .models import ExtractionTool


@admin.register(ExtractionTool)
class ExtractionToolAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "display_name",
        "adapter_kind",
        "is_enabled",
        "is_default",
        "is_mandatory",
    )
    list_editable = ("is_enabled", "is_default", "is_mandatory")
    list_filter = ("adapter_kind", "is_enabled", "is_default", "is_mandatory")
    search_fields = ("name", "display_name")
