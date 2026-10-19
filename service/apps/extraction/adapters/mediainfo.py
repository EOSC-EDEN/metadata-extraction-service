from .base import ErrorCategory, SubprocessToolAdapter


class MediaInfoAdapter(SubprocessToolAdapter):
    tool_name = "mediainfo"
    invocation_command = "mediainfo"
    raw_output_format = "json"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "password"),
        ErrorCategory.WRONG_FORMAT: ("not supported", "unsupported", "unknown format"),
        ErrorCategory.TRUNCATED_OR_BROKEN: ("truncated", "unexpected end", "corrupt"),
    }

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, "--Output=JSON", *self.extra_args, str(file_path)]
