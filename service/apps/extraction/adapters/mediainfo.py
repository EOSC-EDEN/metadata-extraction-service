from .base import SubprocessToolAdapter


class MediaInfoAdapter(SubprocessToolAdapter):
    tool_name = "mediainfo"
    invocation_command = "mediainfo"
    raw_output_format = "json"

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, "--Output=JSON", *self.extra_args, str(file_path)]
