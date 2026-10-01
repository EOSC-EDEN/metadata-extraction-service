from django.apps import AppConfig


class ExtractionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.extraction"
    label = "extraction"

    def ready(self):
        from .adapters.registry import validate_adapter_classes

        validate_adapter_classes()
