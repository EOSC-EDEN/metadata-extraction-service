from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    label = "api"

    def ready(self):
        from .jobs import get_job_service, validate_jobs_config
        from .serializers import validate_upload_limit_config

        validate_upload_limit_config()
        validate_jobs_config()
        get_job_service()
