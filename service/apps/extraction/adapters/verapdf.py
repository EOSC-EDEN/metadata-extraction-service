from django.conf import settings

from .base import ErrorCategory, SubprocessToolAdapter

# Features that describe the file technically. Content structure and interactive features
# (annotations, actions, outlines, form fields, signatures, patterns, shadings, ...) are left out.
DEFAULT_FEATURES = (
    "informationDict,metadata,lowLevelInfo,page,font,iccProfile,colorSpace,outputIntent,"
    "embeddedFile,imageXobject"
)


class VeraPdfAdapter(SubprocessToolAdapter):
    """Runs veraPDF feature extraction (`verapdf --off --extract <features>`) and returns its XML.

    `--off` disables the PDF/A and PDF/UA validation that veraPDF runs by default, so the
    output is a `featuresReport` and never a conformance verdict. The feature list comes from
    `settings.MES_VERAPDF_FEATURES`. veraPDF only understands PDF: any other file is a
    `failure` for this tool only.
    """

    tool_name = "verapdf"
    invocation_command = "verapdf"
    raw_output_format = "xml"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "password"),
    }

    def build_command(self, file_path) -> list[str]:
        features = (getattr(settings, "MES_VERAPDF_FEATURES", "") or "").strip() or DEFAULT_FEATURES
        return [self.invocation_command, "--off", "--extract", features, *self.extra_args, str(file_path)]
