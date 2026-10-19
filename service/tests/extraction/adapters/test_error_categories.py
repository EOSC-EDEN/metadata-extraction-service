from django.test import SimpleTestCase

from apps.extraction.adapters.base import ErrorCategory, SubprocessToolAdapter
from apps.extraction.adapters.exiftool import ExifToolAdapter
from apps.extraction.adapters.ffprobe import FFprobeAdapter
from apps.extraction.adapters.imagemagick import ImageMagickAdapter
from apps.extraction.adapters.mediainfo import MediaInfoAdapter
from apps.extraction.adapters.metaflac import MetaflacAdapter
from apps.extraction.adapters.tika import TikaAdapter

C = ErrorCategory

CASES = {
    ExifToolAdapter: [
        ("Error: File format error", C.WRONG_FORMAT),
        ("Error: Unknown file type", C.WRONG_FORMAT),
        ("Error: Encrypted document", C.ENCRYPTED),
        ("Warning: Truncated PNG image", C.TRUNCATED_OR_BROKEN),
    ],
    MediaInfoAdapter: [
        ("File is encrypted", C.ENCRYPTED),
        ("Format not supported", C.WRONG_FORMAT),
        ("Unexpected end of file", C.TRUNCATED_OR_BROKEN),
    ],
    FFprobeAdapter: [
        ("x.mp4: Invalid data found when processing input", C.WRONG_FORMAT),
        ("moov atom not found", C.TRUNCATED_OR_BROKEN),
        ("Stream is encrypted", C.ENCRYPTED),
    ],
    ImageMagickAdapter: [
        ("identify: no decode delegate for this image format `BIN' @ error/constitute.c/ReadImage/581.", C.WRONG_FORMAT),
        ("identify: insufficient image data in file `a.jpg'", C.TRUNCATED_OR_BROKEN),
        ("This file requires a password for access.", C.ENCRYPTED),
    ],
    MetaflacAdapter: [
        ('ERROR: reading metadata, status = "FLAC__METADATA_CHAIN_STATUS_NOT_A_FLAC_FILE"', C.WRONG_FORMAT),
        ('ERROR: reading metadata, status = "FLAC__METADATA_CHAIN_STATUS_BAD_METADATA"', C.TRUNCATED_OR_BROKEN),
    ],
    TikaAdapter: [
        ("415 Client Error: Unsupported Media Type for url: http://tika/rmeta", C.WRONG_FORMAT),
        ("EncryptedDocumentException: password required", C.ENCRYPTED),
    ],
}


class ClassifyErrorTests(SimpleTestCase):
    def test_representative_messages_per_tool(self):
        for adapter_class, cases in CASES.items():
            for message, expected in cases:
                with self.subTest(tool=adapter_class.tool_name, message=message):
                    self.assertEqual(adapter_class().classify_error(message), expected)

    def test_unmatched_message_is_unknown_for_every_tool(self):
        for adapter_class in CASES:
            with self.subTest(tool=adapter_class.tool_name):
                self.assertEqual(adapter_class().classify_error("something odd"), C.UNKNOWN)
                self.assertEqual(adapter_class().classify_error(""), C.UNKNOWN)

    def test_default_hook_is_unknown(self):
        class Plain(SubprocessToolAdapter):
            tool_name = "plain"

            def build_command(self, file_path):
                return ["plain"]

        self.assertEqual(Plain().classify_error("File is encrypted"), C.UNKNOWN)


class RunAppliesCategoryTests(SimpleTestCase):
    def _adapter(self, exc, classify=None):
        class Boom(SubprocessToolAdapter):
            tool_name = "boom"

            def build_command(self, file_path):
                return ["boom"]

            def invoke(self, file_path):
                raise exc

        if classify:
            Boom.classify_error = classify
        return Boom()

    def test_errors_categories_and_details_stay_aligned(self):
        adapter = ExifToolAdapter()
        adapter.invoke = lambda path: (_ for _ in ()).throw(OSError("Error: File format error"))
        result = adapter.run("f")
        self.assertEqual(result.errors, ["Error: File format error"])
        self.assertEqual([d.category for d in result.error_details], [C.WRONG_FORMAT])

    def test_unmatched_error_is_unknown(self):
        result = self._adapter(OSError("weird")).run("f")
        self.assertEqual([(d.message, d.category) for d in result.error_details], [("weird", C.UNKNOWN)])

    def test_raising_hook_does_not_break_run(self):
        def bad(self, message):
            raise RuntimeError("hook bug")

        result = self._adapter(OSError("weird"), classify=bad).run("f")
        self.assertEqual(result.error_details[0].category, C.UNKNOWN)

    def test_success_has_no_errors(self):
        adapter = ExifToolAdapter()
        adapter.invoke = lambda path: ("[]", "json")
        result = adapter.run("f")
        self.assertEqual((result.errors, result.error_details), ([], ()))
