"""ExtractionOrchestrator: resolve tools -> invoke adapters -> assemble a result.

A plain DTO-returning service, no Django/DRF dependency beyond settings. MES-REQ-3-006: a mandatory
tool's failure surfaces as a top-level error without suppressing the other tools' results — a
non-mandatory tool's failure is just one more per-tool result.

Tool selection lives in `services/tool_resolution.py` (FFIS identification,
then the default tool).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.conf import settings

from apps.extraction.adapters.base import HTTPToolAdapter, ToolExecutionStatus, ToolInvocationResult
from apps.extraction.adapters.registry import get_adapter_class
from apps.extraction.services.tool_resolution import ToolResolution, resolve_tools
from apps.extraction.tool_config import ToolConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregatedExtractionResult:
    resolved_tools: tuple[str, ...]
    selection_method: str
    results: tuple[ToolInvocationResult, ...]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    puid: str | None = None
    mime_type: str | None = None
    #: FFIS's per-engine provenance, present only when FFIS identified the file.
    ffis_provenance: dict | None = None
    identification_url: str | None = None


class ExtractionOrchestrator:
    def __init__(self, ffis_client=None):
        self.ffis_client = ffis_client

    def extract(
        self, file_path, puid=None, mime_type=None, request_id=None
    ) -> AggregatedExtractionResult:
        resolution = resolve_tools(file_path, puid, mime_type, ffis_client=self.ffis_client)
        result = self._run(resolution.tools, resolution.selection_method, file_path, resolution)
        _log_outcome(result, request_id)
        return result

    def _run(
        self, tools, selection_method, file_path, resolution: ToolResolution | None = None
    ) -> AggregatedExtractionResult:
        results = self._invoke_tools(tools, file_path)
        errors = self._mandatory_tool_errors(tools, results)
        warnings = list(resolution.warnings) if resolution else []
        warnings.extend(self._optional_tool_warnings(tools, results))
        return AggregatedExtractionResult(
            resolved_tools=tuple(tool.name for tool in tools),
            selection_method=selection_method,
            results=tuple(results),
            errors=errors,
            warnings=warnings,
            puid=resolution.puid if resolution else None,
            mime_type=resolution.mime_type if resolution else None,
            ffis_provenance=resolution.ffis_provenance if resolution else None,
            identification_url=resolution.identification_url if resolution else None,
        )

    def _invoke_tools(self, tools, file_path) -> list[ToolInvocationResult]:
        if not tools:
            return []
        max_workers = min(len(tools), settings.MES_MAX_CONCURRENT_TOOLS)
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._invoke_one, tool, file_path): tool for tool in tools}
            for future, tool in futures.items():
                try:
                    results.append(future.result())
                except Exception as exc:  # second safety net beyond the adapter's own (run() catches its own)
                    results.append(self._failure_result(tool, exc))
        return results

    def _invoke_one(self, tool: ToolConfig, file_path) -> ToolInvocationResult:
        adapter_class = get_adapter_class(tool.name)
        if issubclass(adapter_class, HTTPToolAdapter):
            adapter = adapter_class(base_url=tool.invocation_command, timeout_seconds=tool.timeout_seconds)
        else:
            adapter = adapter_class(
                invocation_command=tool.invocation_command,  # None keeps the adapter's default
                extra_args=list(tool.extra_args),
                timeout_seconds=tool.timeout_seconds,
            )
        return adapter.run(file_path)

    def _failure_result(self, tool: ToolConfig, exc: Exception) -> ToolInvocationResult:
        now = datetime.now(timezone.utc)
        return ToolInvocationResult(
            tool_name=tool.name,
            tool_version=None,
            status=ToolExecutionStatus.FAILURE,
            raw_output=None,
            raw_output_format=None,
            started_at=now,
            finished_at=now,
            command=None,
            errors=[f"{type(exc).__name__}: {exc}"],
            warnings=[],
        )

    def _mandatory_tool_errors(self, tools, results) -> list[str]:
        results_by_name = {result.tool_name: result for result in results}
        errors = []
        for tool in tools:
            if not tool.is_mandatory:
                continue
            result = results_by_name.get(tool.name)
            if result is None or result.status != ToolExecutionStatus.SUCCESS:
                errors.append(_describe_incomplete(tool.name, result, "Mandatory tool"))
        return errors

    def _optional_tool_warnings(self, tools, results) -> list[str]:
        """Surface non-mandatory tool failures at the top level, not only per tool (MES-REQ-5-001)."""
        results_by_name = {result.tool_name: result for result in results}
        warnings = []
        for tool in tools:
            if tool.is_mandatory:
                continue
            result = results_by_name.get(tool.name)
            if result is None or result.status != ToolExecutionStatus.SUCCESS:
                warnings.append(_describe_incomplete(tool.name, result, "Tool"))
        return warnings


def _log_outcome(result: AggregatedExtractionResult, request_id) -> None:
    """Log every error and warning gathered during extraction, keyed by request_id (MES-REQ-5-001)."""
    for tool_result in result.results:
        extra = {"request_id": request_id, "tool": tool_result.tool_name}
        for message in tool_result.errors:
            logger.error("tool error: %s", message, extra=extra)
        for message in tool_result.warnings:
            logger.warning("tool warning: %s", message, extra=extra)
    extra = {"request_id": request_id}
    for message in result.errors:
        logger.error("extraction error: %s", message, extra=extra)
    for message in result.warnings:
        logger.warning("extraction warning: %s", message, extra=extra)
    logger.info(
        "extraction finished: method=%s tools=%s errors=%d warnings=%d",
        result.selection_method,
        ",".join(result.resolved_tools),
        len(result.errors),
        len(result.warnings),
        extra=extra,
    )


def _describe_incomplete(name, result, label) -> str:
    status = result.status.value if result else "no result"
    message = f"{label} '{name}' did not complete successfully ({status})."
    if result and result.errors:
        message += " " + "; ".join(result.errors)
    return message
