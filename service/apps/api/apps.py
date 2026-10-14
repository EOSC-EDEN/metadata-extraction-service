from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    label = "api"

    def ready(self):
        from .serializers import validate_upload_limit_config

        validate_upload_limit_config()
