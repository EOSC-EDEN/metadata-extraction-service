"""Decide which extraction tools run for a request.

Local format rules (`tool_rules.py`) win when one matches: only the matched tools plus the default
tool run. Otherwise, until the FormatLibraryClient lands, every request runs all enabled
tools (the default tool first). When the caller supplies no PUID/MIME and FFIS is
enabled, FFIS identifies the file and the result is recorded for the
response and provenance. The Format Library hand-off replaces the "always
all enabled tools" part.
"""

import os
from dataclasses import dataclass, field

from django.conf import settings

from apps.extraction.exceptions import FFISUnavailable
from apps.extraction.services.format_identification import FFISClient
from apps.extraction.tool_config import (
    ToolConfig,
    get_default_tool,
    get_tool_config,
    list_tool_configs,
)
from apps.extraction.tool_rules import load_tool_rules, match_rules


@dataclass(frozen=True)
class ToolResolution:
    tools: list[ToolConfig]
    selection_method: str
    puid: str | None = None
    mime_type: str | None = None
    warnings: list[str] = field(default_factory=list)
    ffis_provenance: dict | None = None
    #: Base URL of the identification service (FFIS) when it was consulted for this request.
    identification_url: str | None = None
    #: Names of the local format rules that matched (selection_method local_rule*), in file order.
    matched_rules: list[str] = field(default_factory=list)


def resolve_tools(
    file_path, puid=None, mime_type=None, ffis_client=None, filename=None
) -> ToolResolution:
    """`filename` (the uploaded name) supplies the extension for local format rules."""
    resolution = _identify(file_path, puid, mime_type, ffis_client)
    return _apply_local_rules(resolution, filename)


def _apply_local_rules(resolution: ToolResolution, filename) -> ToolResolution:
    extension = os.path.splitext(filename)[1] if filename else None
    matched = match_rules(load_tool_rules(), resolution.puid, extension)
    if not matched:
        return resolution

    default_tool = resolution.tools[0]
    tools, warnings, skipped = [default_tool], list(resolution.warnings), set()
    for rule in matched:
        for name in rule.tools:
            if name in skipped or any(t.name == name for t in tools):
                continue
            config = get_tool_config(name)
            if config.enabled:
                tools.append(config)
            else:
                skipped.add(name)
                warnings.append(
                    f"tool_rule_disabled_tool: rule '{rule.name}' names the disabled tool '{name}'; skipped."
                )
    # "via FFIS" only when FFIS supplied the PUID that the rules were matched against.
    via_ffis = resolution.selection_method == "default_tool_ffis_identified" and bool(resolution.puid)
    return ToolResolution(
        tools=tools,
        selection_method="local_rule_via_ffis" if via_ffis else "local_rule",
        puid=resolution.puid,
        mime_type=resolution.mime_type,
        warnings=warnings,
        ffis_provenance=resolution.ffis_provenance,
        identification_url=resolution.identification_url,
        matched_rules=[rule.name for rule in matched],
    )


def _identify(file_path, puid, mime_type, ffis_client) -> ToolResolution:
    default_tool = get_default_tool()  # raises ToolNotConfigured (MES-REQ-1-001)

    tools = [default_tool] + [
        t for t in list_tool_configs() if t.enabled and t.name != default_tool.name
    ]

    def resolution(selection_method, **kwargs):
        return ToolResolution(tools=tools, selection_method=selection_method, **kwargs)

    if puid or mime_type:
        return resolution("default_tool_caller_identified", puid=puid, mime_type=mime_type)

    if not settings.MES_FFIS_ENABLED:
        return resolution("default_tool_ffis_disabled")

    client = ffis_client or FFISClient()
    identification_url = getattr(client, "base_url", None) or None
    try:
        identified = client.identify(file_path)
    except FFISUnavailable as exc:
        return resolution(
            "default_tool_ffis_unreachable",
            warnings=[f"ffis_unreachable: {exc}"],
            identification_url=identification_url,
        )
    if identified is None:
        return resolution(
            "default_tool_ffis_inconclusive",
            warnings=["ffis_inconclusive: FFIS could not identify the file's format."],
            identification_url=identification_url,
        )
    return resolution(
        "default_tool_ffis_identified",
        puid=identified.puid,
        mime_type=identified.mime_type,
        warnings=list(identified.engine_warnings),
        ffis_provenance=identified.provenance,
        identification_url=identification_url,
    )
