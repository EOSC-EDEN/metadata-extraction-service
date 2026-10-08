"""Extraction tool configuration, read from `settings.MES_TOOLS`.

Replaces a DB-backed model: how a tool is invoked is deployment config, not
runtime data. `validate_tool_config()` runs from `ExtractionConfig.ready()`
so a bad entry fails at startup, not mid-request.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.extraction.exceptions import ToolNotConfigured

KINDS = ("subprocess", "http")


@dataclass(frozen=True)
class ToolConfig:
    name: str
    adapter: str
    kind: str
    invocation_command: str
    extra_args: tuple[str, ...] = ()
    timeout_seconds: int = 30
    enabled: bool = True
    mandatory: bool = False

    @property
    def is_mandatory(self) -> bool:
        return self.mandatory


def _build(name: str, entry: dict) -> ToolConfig:
    try:
        config = ToolConfig(
            name=name,
            adapter=entry["adapter"],
            kind=entry["kind"],
            invocation_command=entry["invocation_command"],
            extra_args=tuple(entry.get("extra_args", ())),
            timeout_seconds=entry.get("timeout_seconds", 30),
            enabled=entry.get("enabled", True),
            mandatory=entry.get("mandatory", False),
        )
    except KeyError as exc:
        raise ImproperlyConfigured(
            f"settings.MES_TOOLS['{name}'] is missing required key {exc}."
        ) from exc
    if config.kind not in KINDS:
        raise ImproperlyConfigured(
            f"settings.MES_TOOLS['{name}']['kind'] must be one of {KINDS}, got '{config.kind}'."
        )
    return config


def get_tool_config(name: str) -> ToolConfig:
    try:
        entry = settings.MES_TOOLS[name]
    except KeyError as exc:
        raise ToolNotConfigured(f"No tool '{name}' in settings.MES_TOOLS.") from exc
    return _build(name, entry)


def get_default_tool() -> ToolConfig:
    """The enabled default tool (MES-REQ-1-001), or raise ToolNotConfigured."""
    name = settings.MES_DEFAULT_TOOL
    if not name or name not in settings.MES_TOOLS:
        raise ToolNotConfigured(
            f"settings.MES_DEFAULT_TOOL ('{name}') does not name a tool in settings.MES_TOOLS (MES-REQ-1-001)."
        )
    config = get_tool_config(name)
    if not config.enabled:
        raise ToolNotConfigured(f"The default tool '{name}' is disabled (MES-REQ-1-001).")
    return config


def validate_tool_config() -> None:
    for name, entry in settings.MES_TOOLS.items():
        _build(name, entry)
