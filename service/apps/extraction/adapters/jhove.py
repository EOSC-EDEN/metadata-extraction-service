import subprocess

from .base import ErrorCategory, SubprocessToolAdapter


class JhoveAdapter(SubprocessToolAdapter):
    """Runs `jhove -h xml` and returns its XML report unmodified.

    MES uses JHOVE for technical metadata only. JHOVE has no option to turn validation off,
    so its report always carries a validity `status` and `messages` next to the `properties`.
    They stay in the raw output (MES-REQ-3-004) but are not an MES verdict: they never become
    tool errors, warnings or PREMIS outcome notes. The tool status is `success` whenever JHOVE
    produced a report, and `failure`/`timeout` only when it could not run.
    """

    tool_name = "jhove"
    invocation_command = "jhove"
    raw_output_format = "xml"
    error_patterns = {
        ErrorCategory.ENCRYPTED: ("encrypt", "password"),
    }

    def build_command(self, file_path) -> list[str]:
        return [self.invocation_command, "-k", "-h", "xml", *self.extra_args, str(file_path)]

    def invoke(self, file_path) -> tuple[str, str]:
        try:
            return super().invoke(file_path)
        except subprocess.CalledProcessError as exc:
            # A report on stdout means JHOVE ran, whatever its exit code says about the file.
            if "<jhove" in (exc.stdout or ""):
                return exc.stdout, self.raw_output_format
            raise
