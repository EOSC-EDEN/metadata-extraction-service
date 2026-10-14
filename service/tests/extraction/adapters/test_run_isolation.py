from unittest import TestCase, mock

from apps.extraction.adapters.exiftool import ExifToolAdapter


class UnexpectedExceptionTests(TestCase):
    def test_run_never_raises_and_names_the_exception_type(self):
        with mock.patch(
            "apps.extraction.adapters.base.subprocess.run",
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"),
        ):
            result = ExifToolAdapter().run("/tmp/x")
        self.assertEqual(result.status.value, "failure")
        self.assertTrue(result.errors[0].startswith("UnicodeDecodeError"))
