import json

from django.conf import settings

from .base import ErrorCategory, SubprocessToolAdapter


class FileScraperAdapter(SubprocessToolAdapter):
    """Runs the File Scraper CLI (`scraper scrape-file --tool-info`) and returns its JSON.

    File Scraper is only ever called as a command-line tool, never imported as a library.
    It exits 0 even for a file that is not well-formed: that verdict is only in the JSON
    (`well-formed: false` plus an `errors` object keyed by extractor). `invoke()` reads
    that verdict, without touching the output, and reports each message as a warning
    (MES-REQ-5-001), the same way the other tools report problems on exit 0. A non-zero exit
    means a bad argument or a crash and is a `failure` for this tool only.
    """

    tool_name = "file_scraper"
    invocation_command = "scraper"
    raw_output_format = "json"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "password"),
        ErrorCategory.TRUNCATED_OR_BROKEN: (
            "truncated",
            "premature end",
            "unexpected end",
            "corrupt",
            "not well-formed",
            "invalid",
        ),
        ErrorCategory.WRONG_FORMAT: ("mime type", "mimetype", "not a valid", "unsupported"),
    }

    def build_command(self, file_path) -> list[str]:
        command = [self.invocation_command, "scrape-file", "--tool-info"]
        if getattr(settings, "MES_FILE_SCRAPER_SKIP_WELLFORMED", False):
            command.append("--skip-wellformed-check")
        return [*command, *self.extra_args, str(file_path)]

    def invoke(self, file_path) -> tuple[str, str]:
        raw_output, raw_output_format = super().invoke(file_path)
        self._last_warnings = [*self._last_warnings, *_wellformed_warnings(raw_output)]
        return raw_output, raw_output_format


def _wellformed_warnings(raw_output: str) -> list[str]:
    """Messages for a file File Scraper reports as not well-formed; the output is only read."""
    try:
        document = json.loads(raw_output)
    except ValueError:
        return []
    if not isinstance(document, dict) or document.get("well-formed") is not False:
        return []  # `well-formed` is absent (not null) with --skip-wellformed-check
    errors = document.get("errors")
    messages = [
        f"{extractor}: {message}"
        for extractor, entries in (errors.items() if isinstance(errors, dict) else ())
        for message in (entries if isinstance(entries, list) else [entries])
    ]
    return messages or ["File Scraper reports the file is not well-formed."]
