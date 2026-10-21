import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.api.uploads import save_upload


class SaveUploadTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = self._tmp.name

    def test_writes_the_content_and_keeps_the_suffix(self):
        upload = SimpleUploadedFile("photo.final.JPG", b"abc")
        path = save_upload(upload, self.directory)

        self.assertEqual(os.path.dirname(path), self.directory)
        self.assertEqual(os.path.basename(path), "upload.JPG")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"abc")

    def test_name_without_a_suffix_gets_none(self):
        self.assertEqual(os.path.basename(save_upload(SimpleUploadedFile("README", b"x"), self.directory)), "upload")

    def test_uploaded_name_cannot_escape_the_directory(self):
        path = save_upload(SimpleUploadedFile("../../evil.sh", b"x"), self.directory)
        self.assertEqual(os.path.dirname(path), self.directory)

    def test_chunks_larger_than_one_read_are_written_in_full(self):
        content = os.urandom(3 * 64 * 1024 + 1)
        path = save_upload(SimpleUploadedFile("big.bin", content), self.directory)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), content)
