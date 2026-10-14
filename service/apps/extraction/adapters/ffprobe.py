from .base import SubprocessToolAdapter


class FFprobeAdapter(SubprocessToolAdapter):
    tool_name = "ffprobe"
    invocation_command = "ffprobe"
    raw_output_format = "json"

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
