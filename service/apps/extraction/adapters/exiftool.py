from .base import SubprocessToolAdapter


class ExifToolAdapter(SubprocessToolAdapter):
    tool_name = "exiftool"
    invocation_command = "exiftool"
    raw_output_format = "json"

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, "-json", *self.extra_args, str(file_path)]
