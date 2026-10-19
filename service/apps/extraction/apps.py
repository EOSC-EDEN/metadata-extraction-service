from django.apps import AppConfig


class ExtractionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.extraction"
    label = "extraction"

    def ready(self):
        from .adapters.registry import validate_adapter_classes
        from .tool_config import validate_concurrency_config, validate_tool_config
        from .tool_rules import validate_tool_rules

        validate_tool_config()
        validate_concurrency_config()
        validate_tool_rules()
        validate_adapter_classes()
