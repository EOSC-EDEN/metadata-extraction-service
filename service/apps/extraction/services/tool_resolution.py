"""Decide which extraction tools run for a request.

Until the FormatLibraryClient lands, every request runs only the
configured default tool. When the caller supplies no PUID/MIME and FFIS is
enabled, FFIS identifies the file and the result is recorded for the
response and provenance. The Format Library hand-off replaces the "always
default tool" part.
"""

from dataclasses import dataclass, field

from django.conf import settings

from apps.extraction.exceptions import FFISUnavailable, ToolNotConfigured
from apps.extraction.models import ExtractionTool
from apps.extraction.services.format_identification import FFISClient


@dataclass(frozen=True)
class ToolResolution:
    tools: list[ExtractionTool]
    selection_method: str
    puid: str | None = None
    mime_type: str | None = None
    warnings: list[str] = field(default_factory=list)
    ffis_provenance: dict | None = None


def resolve_tools(file_path, puid=None, mime_type=None, ffis_client=None) -> ToolResolution:
    default_tool = ExtractionTool.objects.filter(is_enabled=True, is_default=True).first()
    if default_tool is None:
        raise ToolNotConfigured("No enabled default ExtractionTool is configured (MES-REQ-1-001).")

    def resolution(selection_method, **kwargs):
        return ToolResolution(
            tools=[default_tool], selection_method=selection_method, **kwargs
        )

    if puid or mime_type:
        return resolution("default_tool_caller_identified", puid=puid, mime_type=mime_type)

    if not settings.MES_FFIS_ENABLED:
        return resolution("default_tool_ffis_disabled")

    client = ffis_client or FFISClient()
    try:
        identified = client.identify(file_path)
    except FFISUnavailable as exc:
        return resolution(
            "default_tool_ffis_unreachable", warnings=[f"ffis_unreachable: {exc}"]
        )
    if identified is None:
        return resolution(
            "default_tool_ffis_inconclusive",
            warnings=["ffis_inconclusive: FFIS could not identify the file's format."],
        )
    return resolution(
        "default_tool_ffis_identified",
        puid=identified.puid,
        mime_type=identified.mime_type,
        warnings=list(identified.engine_warnings),
        ffis_provenance=identified.provenance,
    )
