"""Adapter framework (MES-REQ-3-003/004/007/008, MES-REQ-4-001..007).

Tool adapters never reshape or aggregate output — they only invoke a tool
and label/decode whatever it prints, unmodified (MES-REQ-3-004). Adapter-
level failure isolation lives here, in `run()`, not in the orchestrator:
a `run()` call never raises — it always returns a `ToolInvocationResult`
with a FAILURE/TIMEOUT status instead.
"""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import requests


class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ToolInvocationResult:
    tool_name: str
    tool_version: str | None
    status: ToolExecutionStatus
    raw_output: str | None
    raw_output_format: str | None
    started_at: datetime
    finished_at: datetime
    command: tuple[str, ...] | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BaseToolAdapter(ABC):
    """Template for invoking one extraction tool and reporting the outcome."""

    tool_name: str

    #: Exceptions from `invoke()` that mean "the tool didn't finish in time".
    timeout_exceptions: tuple[type[Exception], ...] = ()
    #: Exceptions from `invoke()` that mean "the tool failed" (any other way).
    failure_exceptions: tuple[type[Exception], ...] = (Exception,)

    def __init__(self, tool_name: str | None = None):
        if tool_name is not None:
            self.tool_name = tool_name
        self._last_command: tuple[str, ...] | None = None
        self._last_warnings: list[str] = []

    @abstractmethod
    def invoke(self, file_path) -> tuple[str, str]:
        """Run the tool against `file_path`, return (raw_output, raw_output_format).

        May raise; `run()` catches `timeout_exceptions`/`failure_exceptions`
        and maps them to a status instead of propagating them.
        """

    def get_tool_version(self) -> str | None:
        return None

    def run(self, file_path) -> ToolInvocationResult:
        started_at = datetime.now(timezone.utc)
        raw_output: str | None = None
        raw_output_format: str | None = None
        errors: list[str] = []
        try:
            raw_output, raw_output_format = self.invoke(file_path)
            status = ToolExecutionStatus.SUCCESS
        except self.timeout_exceptions as exc:
            status = ToolExecutionStatus.TIMEOUT
            errors = [str(exc)]
        except self.failure_exceptions as exc:
            status = ToolExecutionStatus.FAILURE
            errors = [_describe_failure(exc)]
        except Exception as exc:  # run() never raises, whatever invoke() throws
            status = ToolExecutionStatus.FAILURE
            errors = [f"{type(exc).__name__}: {exc}"]
        finished_at = datetime.now(timezone.utc)
        return ToolInvocationResult(
            tool_name=self.tool_name,
            tool_version=self.get_tool_version(),
            status=status,
            raw_output=raw_output,
            raw_output_format=raw_output_format,
            started_at=started_at,
            finished_at=finished_at,
            command=self._last_command,
            errors=errors,
            warnings=list(self._last_warnings),
        )


def _describe_failure(exc: Exception) -> str:
    stderr = getattr(exc, "stderr", None)
    return f"{exc}: {stderr}" if stderr else str(exc)


class SubprocessToolAdapter(BaseToolAdapter):
    """Invokes a CLI tool via subprocess. Used by ExifTool, MediaInfo, etc."""

    invocation_command: str = ""
    raw_output_format: str = "text"
    timeout_seconds: int = 30

    timeout_exceptions = (subprocess.TimeoutExpired,)
    failure_exceptions = (OSError, subprocess.CalledProcessError)

    def __init__(
        self,
        tool_name: str | None = None,
        invocation_command: str | None = None,
        extra_args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ):
        super().__init__(tool_name=tool_name)
        if invocation_command is not None:
            self.invocation_command = invocation_command
        self.extra_args = list(extra_args) if extra_args else []
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds

    @abstractmethod
    def build_command(self, file_path) -> list[str]:
        """Return the full argv for invoking this tool against `file_path`."""

    def invoke(self, file_path) -> tuple[str, str]:
        command = self.build_command(file_path)
        self._last_command = tuple(command)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=True,
        )
        # A tool that exits 0 but writes to stderr is reporting warnings (MES-REQ-5-001).
        if result.stderr and result.stderr.strip():
            self._last_warnings = [result.stderr.strip()]
        return result.stdout, self.raw_output_format


class HTTPToolAdapter(BaseToolAdapter):
    """Invokes a tool over HTTP. Used by externally hosted services (MES-REQ-1-005)."""

    base_url: str = ""
    raw_output_format: str = "text"
    timeout_seconds: int = 30

    timeout_exceptions = (requests.exceptions.Timeout,)
    failure_exceptions = (requests.exceptions.RequestException,)

    def __init__(
        self,
        tool_name: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ):
        super().__init__(tool_name=tool_name)
        if base_url is not None:
            self.base_url = base_url
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds

    @abstractmethod
    def build_request(self, file_path) -> dict:
        """Return kwargs for `requests.request(**kwargs)` to invoke this tool."""

    def invoke(self, file_path) -> tuple[str, str]:
        request_kwargs = self.build_request(file_path)
        self._last_command = (request_kwargs.get("method", "POST"), request_kwargs.get("url", ""))
        response = requests.request(timeout=self.timeout_seconds, **request_kwargs)
        response.raise_for_status()
        return response.text, self.raw_output_format
