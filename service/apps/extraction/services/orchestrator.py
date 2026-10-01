"""ExtractionOrchestrator: resolve tools -> invoke adapters -> assemble a result.

A plain DTO-returning service, no Django/DRF dependency beyond the ORM read
in `_resolve_tools`. MES-REQ-3-006: a mandatory tool's failure surfaces as a
top-level error without suppressing the other tools' results — a
non-mandatory tool's failure is just one more per-tool result.

`_resolve_tools` is currently a stub: it always returns the one DB-configured
default ExtractionTool, ignoring `puid`/`mime_type`. Real Format Library /
FFIS driven resolution replaces it in `tool_resolution.py`.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.conf import settings

from apps.extraction.adapters.base import HTTPToolAdapter, ToolExecutionStatus, ToolInvocationResult
from apps.extraction.adapters.registry import get_adapter_class
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.models import ExtractionTool


@dataclass(frozen=True)
class AggregatedExtractionResult:
    resolved_tools: tuple[str, ...]
    selection_method: str
    results: tuple[ToolInvocationResult, ...]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ExtractionOrchestrator:
    def extract(self, file_path, puid=None, mime_type=None) -> AggregatedExtractionResult:
        tools, selection_method = self._resolve_tools(puid, mime_type)
        return self._run(tools, selection_method, file_path)

    def _resolve_tools(self, puid, mime_type) -> tuple[list[ExtractionTool], str]:
        default_tool = ExtractionTool.objects.filter(is_enabled=True, is_default=True).first()
        if default_tool is None:
            raise ToolNotConfigured(
                "No enabled default ExtractionTool is configured (MES-REQ-1-001)."
            )
        return [default_tool], "default_tool"

    def _run(self, tools, selection_method, file_path) -> AggregatedExtractionResult:
        results = self._invoke_tools(tools, file_path)
        errors = self._mandatory_tool_errors(tools, results)
        return AggregatedExtractionResult(
            resolved_tools=tuple(tool.name for tool in tools),
            selection_method=selection_method,
            results=tuple(results),
            errors=errors,
            warnings=[],
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

    def _invoke_one(self, tool: ExtractionTool, file_path) -> ToolInvocationResult:
        adapter_class = get_adapter_class(tool.name)
        if issubclass(adapter_class, HTTPToolAdapter):
            adapter = adapter_class(base_url=tool.invocation_command, timeout_seconds=tool.timeout_seconds)
        else:
            adapter = adapter_class(
                invocation_command=tool.invocation_command,
                extra_args=tool.extra_args,
                timeout_seconds=tool.timeout_seconds,
            )
        return adapter.run(file_path)

    def _failure_result(self, tool: ExtractionTool, exc: Exception) -> ToolInvocationResult:
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
            errors=[str(exc)],
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
                errors.append(f"Mandatory tool '{tool.name}' did not complete successfully.")
        return errors
