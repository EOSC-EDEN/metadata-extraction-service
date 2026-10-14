from .base import SubprocessToolAdapter


class ImageMagickAdapter(SubprocessToolAdapter):
    """Runs ImageMagick's `identify -verbose`.

    ImageMagick 7 also ships `identify`; to use the unified binary instead set
    `invocation_command` to `magick` and `extra_args` to `["identify"]`.
    """

    tool_name = "imagemagick"
    invocation_command = "identify"
    raw_output_format = "text"

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, *self.extra_args, "-verbose", str(file_path)]
