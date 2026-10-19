from .base import ErrorCategory, SubprocessToolAdapter


class ExifToolAdapter(SubprocessToolAdapter):
    tool_name = "exiftool"
    invocation_command = "exiftool"
    raw_output_format = "json"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "password"),
        ErrorCategory.WRONG_FORMAT: ("unknown file type", "file format error", "unsupported file type"),
        ErrorCategory.TRUNCATED_OR_BROKEN: ("truncated", "end of processing", "file is empty", "corrupt"),
    }

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, "-json", *self.extra_args, str(file_path)]
