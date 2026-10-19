from .base import ErrorCategory, SubprocessToolAdapter


class FFprobeAdapter(SubprocessToolAdapter):
    tool_name = "ffprobe"
    invocation_command = "ffprobe"
    raw_output_format = "json"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "decryption", "drm"),
        ErrorCategory.TRUNCATED_OR_BROKEN: ("moov atom not found", "truncated", "partial file", "unexpected end", "corrupt"),
        ErrorCategory.WRONG_FORMAT: ("invalid data found when processing input", "unknown format", "could not find codec"),
    }

    def build_command(self, file_path) -> list[str]:
        return [
            self.invocation_command,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            *self.extra_args,
            str(file_path),
        ]
