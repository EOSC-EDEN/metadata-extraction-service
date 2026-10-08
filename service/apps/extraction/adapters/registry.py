"""Resolves a tool name to its adapter class.

The mapping lives in `settings.MES_TOOLS[name]["adapter"]` (dotted
class path) so new tools can be added without touching this module
(MES-REQ-4-007). `validate_adapter_classes()` is called from
`ExtractionConfig.ready()` so a misconfigured/missing adapter fails at
Django startup, not mid-request.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def get_adapter_class(tool_name: str):
    try:
        dotted_path = settings.MES_TOOLS[tool_name]["adapter"]
    except KeyError as exc:
        raise ImproperlyConfigured(
            f"No adapter class configured for tool '{tool_name}' in settings.MES_TOOLS."
        ) from exc
    return import_string(dotted_path)


def validate_adapter_classes() -> None:
    for tool_name, entry in settings.MES_TOOLS.items():
        dotted_path = entry.get("adapter", "")
        try:
            import_string(dotted_path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"settings.MES_TOOLS['{tool_name}']['adapter'] = '{dotted_path}' "
                f"could not be imported: {exc}"
            ) from exc
