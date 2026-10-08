# Adding an extraction tool

This guide explains how to add a new metadata extraction tool to the Metadata Extraction Service (MES). Adding a tool never requires changes to the orchestration code (MES-REQ-4-007). It takes three things:

1. An **adapter class** that knows how to invoke the tool.
2. An **entry in `MES_TOOLS`** (Django settings) that tells MES the adapter exists and how to run it.
3. The **tool itself** installed where the service runs (for the Docker image, a build argument).

All paths below are relative to the `service/` directory.

## How the pieces fit together

```
settings.MES_TOOLS["mytool"]            apps/extraction/adapters/mytool.py
  adapter: "...MyToolAdapter"  ───────►  class MyToolAdapter(SubprocessToolAdapter)
  kind, invocation_command,                 build_command(file_path) -> argv
  extra_args, timeout_seconds,
  enabled, mandatory
        │
        ▼
tool_config.py  validates the entry at startup (ExtractionConfig.ready())
registry.py     imports the adapter class from its dotted path
orchestrator.py instantiates it with the config values and calls adapter.run(file)
```

Key properties to keep in mind:

- **Raw output is returned unmodified** (MES-REQ-3-004). An adapter only runs the tool and labels its output; it must not parse, reshape or merge it.
- **Failures are isolated.** `adapter.run()` never raises. Non-zero exits, missing binaries and timeouts become a `failure` or `timeout` status on that tool's result, and other tools' results are still returned. If the tool is `mandatory`, its failure additionally produces a top-level error.
- **Misconfiguration fails at startup**, not mid-request: every `MES_TOOLS` entry is validated and every adapter path is imported when Django starts.

## Step 1: Write the adapter

Create `apps/extraction/adapters/<tool>.py`. For a command-line tool, subclass `SubprocessToolAdapter` and implement `build_command()`.

Example, for FFprobe:

```python
from .base import SubprocessToolAdapter


class FFprobeAdapter(SubprocessToolAdapter):
    tool_name = "ffprobe"
    invocation_command = "ffprobe"
    raw_output_format = "json"

    def build_command(self, file_path) -> list[str]:
        return [
            self.invocation_command,
            "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            *self.extra_args,
            str(file_path),
        ]
```

What each part does:

| Attribute / method | Purpose |
|---|---|
| `tool_name` | Name reported in results. **Must equal the key you use in `MES_TOOLS`** (see [Gotchas](#gotchas)). |
| `invocation_command` | Default binary. Used unless `MES_TOOLS` sets `invocation_command`, which overrides it. |
| `raw_output_format` | Label for the output: `"json"`, `"xml"` or `"text"`. Describes what the tool prints; MES does not convert it. |
| `build_command(file_path)` | Returns the full argv as a list (no shell). Put `*self.extra_args` before the file path so settings-supplied flags are applied. |
| `get_tool_version()` *(optional)* | Return the tool's version string, reported in the result's tool metadata. Defaults to `None`. |

Behaviour inherited from `SubprocessToolAdapter`, which you normally do not override:

- The command runs with `check=True` and the configured timeout.
- Exit code ≠ 0 → `failure` (stderr is included in the error message).
- `subprocess.TimeoutExpired` → `timeout`.
- Exit code 0 but text on stderr → `success` with that text recorded as a warning (MES-REQ-5-001).
- The executed command is recorded in the result for provenance.

### Tools that run as an HTTP service

For an externally hosted tool (MES-REQ-1-005), such as an Apache Tika server, subclass `HTTPToolAdapter` and implement `build_request()` instead. It returns the keyword arguments for `requests.request(...)`:

```python
from .base import HTTPToolAdapter


class TikaAdapter(HTTPToolAdapter):
    tool_name = "tika"
    raw_output_format = "json"

    def build_request(self, file_path) -> dict:
        with open(file_path, "rb") as handle:
            body = handle.read()
        return {
            "method": "PUT",
            "url": f"{self.base_url}/rmeta/text",
            "headers": {"Accept": "application/json"},
            "data": body,
        }
```

For HTTP tools the required `invocation_command` setting holds the **base URL** (for example `http://tika:9998`) and is available as `self.base_url`. `extra_args` is not passed to HTTP adapters. Timeouts map to `timeout`; connection errors and non-2xx responses map to `failure`.

### Tools that need something else

If a tool is neither a CLI nor an HTTP service, subclass `BaseToolAdapter` and implement `invoke(file_path) -> (raw_output, raw_output_format)`. You may raise from `invoke()`; list the exception types in `timeout_exceptions` and `failure_exceptions` so `run()` can map them to a status. Note that the orchestrator only constructs `SubprocessToolAdapter` and `HTTPToolAdapter` subclasses with their config arguments (`HTTPToolAdapter` subclasses get `base_url`, everything else gets `invocation_command`, `extra_args` and `timeout_seconds`), so a custom base class must accept those keyword arguments.

## Step 2: Register the tool in `MES_TOOLS`

Add an entry to `MES_TOOLS` in `web/settings/base.py`. Environment-specific overrides can go in `dev.py`, `prod.py` or `test.py`.

```python
MES_TOOLS = {
    # ... existing tools ...
    "ffprobe": {
        "adapter": "apps.extraction.adapters.ffprobe.FFprobeAdapter",
        "kind": "subprocess",
        # Optional: omit to use the adapter's default ("ffprobe" from PATH).
        "invocation_command": os.environ.get("MES_FFPROBE_COMMAND", "ffprobe"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": True,
        "mandatory": False,
    },
}
```

### Configuration keys

| Key | Required | Default | Description |
|---|---|---|---|
| `adapter` | yes | – | Dotted path to the adapter class. Imported at startup; an import error stops the service from starting. |
| `kind` | yes | – | `"subprocess"` (local CLI) or `"http"` (externally hosted). Any other value is rejected at startup. |
| `invocation_command` | `http`: yes; `subprocess`: no | adapter's `invocation_command` | Binary name or absolute path for `subprocess` tools; set it only to override the adapter's default (for example a non-`PATH` install). Base URL for `http` tools, where it is required because adapters have no default URL. |
| `extra_args` | no | `[]` | Additional arguments inserted by the adapter's `build_command()` (subprocess tools only). |
| `timeout_seconds` | no | `30` | Per-invocation timeout. Increase for slow tools or large files. |
| `enabled` | no | `True` | Disabled tools stay listed by `GET /api/v1/tools/` but are never selected. |
| `mandatory` | no | `False` | If `True`, a failure or timeout of this tool is reported as a top-level error (MES-REQ-3-006). If `False`, it is reported as a warning and the other tools' results are still returned. |

The dictionary key (`"ffprobe"` above) is the tool's name everywhere: in the `tools` listing, in result output, and when selecting the default tool.

### Making it the default tool

At present, every request runs the **default tool** (MES-REQ-1-001). Mapping file formats to several tools through the File Format Library is a later build step. To make the new tool the default, set the `MES_DEFAULT_TOOL` environment variable to its key:

```bash
MES_DEFAULT_TOOL=ffprobe
```

The default tool must exist in `MES_TOOLS` and be `enabled`, or requests fail with a "tool not configured" error.

## Step 3: Install the tool

The service only calls the binary; it does not install it.

- **Local development:** install the tool with your package manager and make sure it is on `PATH` (or set the `invocation_command` to an absolute path).
- **Docker:** the `Dockerfile` installs tools via `INSTALL_<TOOL>` build arguments. FFprobe (package `ffmpeg`) and ImageMagick already have switches. For a tool without one:
  1. Add `ARG INSTALL_MYTOOL=false` to the `Dockerfile`.
  2. Add a line to the package list, for example `[ "$INSTALL_MYTOOL" = "true" ] && packages="$packages <apt-package>"`.
  3. Pass the argument through in `docker-compose.yml` under `build.args`, and add it to `.env.example`.

  Tools that are not in Debian's repositories (or that are Java services like Tika) need their own install steps, or should run as a separate container used through an `http` adapter.
- **Overriding the binary path:** if you read the command from an environment variable, as the built-in tools do (`MES_EXIFTOOL_COMMAND`, `MES_MEDIAINFO_COMMAND`), add that variable to `docker-compose.yml` as well if you want to set it there.

## Step 4: Add tests

Mirror the existing tests in `tests/extraction/adapters/`. Mock `subprocess.run` so the tests do not need the tool installed:

```python
import subprocess
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.ffprobe import FFprobeAdapter


class FFprobeAdapterTests(SimpleTestCase):
    def test_build_command(self):
        command = FFprobeAdapter(timeout_seconds=5).build_command("/tmp/file.mp4")
        self.assertEqual(command[0], "ffprobe")
        self.assertEqual(command[-1], "/tmp/file.mp4")

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_success_returns_raw_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout='{"format":{}}', stderr=""
        )
        result = FFprobeAdapter(timeout_seconds=5).run("/tmp/file.mp4")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, '{"format":{}}')

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_non_zero_exit_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["ffprobe"], stderr="invalid data"
        )
        result = FFprobeAdapter(timeout_seconds=5).run("/tmp/file.mp4")
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_timeout_maps_to_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=5)
        result = FFprobeAdapter(timeout_seconds=5).run("/tmp/file.mp4")
        self.assertEqual(result.status, ToolExecutionStatus.TIMEOUT)
```

Run the suite from `service/`:

```bash
python manage.py test
```

## Step 5: Verify

1. Start the service (`python manage.py runserver`, or `docker compose up --build`). A bad entry or unimportable adapter stops startup with an `ImproperlyConfigured` message naming the offending key.
2. Check that the tool is listed:

   ```bash
   curl http://localhost:8000/api/v1/tools/
   ```

   The response shows name, kind, enabled, mandatory, default and timeout. Invocation details are deliberately not exposed.
3. If the tool is the default, run an extraction:

   ```bash
   curl -F file=@sample.mp4 http://localhost:8000/api/v1/extract/
   ```

   The tool's raw output appears in the result together with its name, version, status and the command that was run.

## Gotchas

- **The `MES_TOOLS` key must match the adapter's `tool_name`.** Results are matched back to configured tools by name, so a mismatch makes a successful run look like "no result", and a `mandatory` tool would then be reported as failed.
- **Do not parse or "fix" output in the adapter.** Harmonised metadata belongs in a separate layer; the adapter's output is the tool's raw output.
- **Build the argv as a list.** Never join it into a shell string, since uploaded file names are untrusted.
- **Choose flags that make the tool exit non-zero on failure.** Failure detection relies on the exit code; tools that exit 0 on error will be reported as successful.
- **Set a realistic `timeout_seconds`.** The Gunicorn worker timeout in the `Dockerfile` is 120 s; a tool timeout above that will be cut short by the server.
- **Disabling is safer than deleting.** Set `"enabled": False` to take a tool out of use while keeping it visible in `GET /api/v1/tools/`.
