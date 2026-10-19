from .base import ErrorCategory, SubprocessToolAdapter


class ImageMagickAdapter(SubprocessToolAdapter):
    """Runs ImageMagick's `identify -verbose`.

    ImageMagick 7 also ships `identify`; to use the unified binary instead set
    `invocation_command` to `magick` and `extra_args` to `["identify"]`.
    """

    tool_name = "imagemagick"
    invocation_command = "identify"
    raw_output_format = "text"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("password", "encrypt"),
        ErrorCategory.WRONG_FORMAT: ("no decode delegate", "improper image header", "unrecognized image format", "not a supported image"),
        ErrorCategory.TRUNCATED_OR_BROKEN: ("corrupt image", "insufficient image data", "premature end", "unexpected end", "truncated"),
    }

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, *self.extra_args, "-verbose", str(file_path)]
