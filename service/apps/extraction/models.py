from django.db import models
from django.db.models import Q


class ExtractionTool(models.Model):
    """Local configuration for one extraction tool adapter.

    Distinct from the external Format Library (resolves PUID/MIME -> tool
    names) and FFIS (identifies a file's format) — this model only holds how
    to invoke a tool once its name has been resolved.
    """

    class AdapterKind(models.TextChoices):
        SUBPROCESS = "subprocess", "Subprocess"
        HTTP = "http", "HTTP"

    name = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Matches the adapter registry key (settings.MES_ADAPTER_CLASSES).",
    )
    display_name = models.CharField(max_length=150)
    adapter_kind = models.CharField(max_length=20, choices=AdapterKind.choices)
    invocation_command = models.CharField(
        max_length=500,
        help_text="Binary path (+ args) for a subprocess tool, or base URL for an HTTP tool.",
    )
    extra_args = models.JSONField(default=list, blank=True)
    timeout_seconds = models.PositiveIntegerField(default=30)
    is_enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text="Used when no format mapping resolves a tool (MES-REQ-1-001).",
    )
    is_mandatory = models.BooleanField(
        default=False,
        help_text="Failure of this tool surfaces a top-level error (MES-REQ-3-006).",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="unique_default_extraction_tool",
            ),
        ]

    def __str__(self):
        return self.display_name or self.name
