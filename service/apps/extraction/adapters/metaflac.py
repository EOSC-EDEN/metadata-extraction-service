from .base import SubprocessToolAdapter


class MetaflacAdapter(SubprocessToolAdapter):
    """Runs `metaflac --list` for the STREAMINFO and VORBIS_COMMENT blocks of a FLAC file.

    metaflac only understands FLAC: for any other file it exits non-zero, which is
    reported as a `failure` for this tool only.
    """

    tool_name = "metaflac"
    invocation_command = "metaflac"
    raw_output_format = "text"

    def build_command(self, file_path) -> list[str]:
        return [
            self.invocation_command,
            "--list",
            "--block-type=STREAMINFO,VORBIS_COMMENT",
            *self.extra_args,
            str(file_path),
        ]
