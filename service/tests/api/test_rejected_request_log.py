"""A rejected request is logged with what was asked for, so a 406 or 400 can be traced to its client."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

LOGGER = "apps.api.exceptions"


def _file():
    return SimpleUploadedFile("a.jpg", b"data", content_type="image/jpeg")


class RejectedRequestLogTests(TestCase):
    def _log(self, level="WARNING"):
        return self.assertLogs(LOGGER, level=level)

    def test_a_406_names_the_method_the_path_and_the_accept_header(self):
        for accept in ("application/xml", "text/xml", "application/xml;q=1.0, text/xml;q=0.9"):
            with self.subTest(accept=accept), self._log() as logs:
                response = APIClient().post(
                    "/api/v1/extract/", {"file": _file()}, format="multipart", HTTP_ACCEPT=accept
                )
                self.assertEqual(response.status_code, 406)
            message = logs.records[0].getMessage()
            self.assertIn("(406)", message)
            self.assertIn("POST /api/v1/extract/", message)
            self.assertIn(f"Accept: {accept}", message)

    def test_a_406_on_a_json_only_route_is_traced_the_same_way(self):
        with self._log() as logs:
            APIClient().get("/api/v1/jobs/some-job/", HTTP_ACCEPT="application/premis+xml")
        message = logs.records[0].getMessage()
        self.assertIn("GET /api/v1/jobs/some-job/", message)
        self.assertIn("Accept: application/premis+xml", message)

    def test_a_missing_accept_header_is_shown_as_such(self):
        with self._log() as logs:
            APIClient().post("/api/v1/extract/", {}, format="multipart")  # a 400, no Accept header
        message = logs.records[0].getMessage()
        self.assertIn("(400)", message)
        self.assertIn("POST /api/v1/extract/", message)
        self.assertNotIn("Accept:", message)  # only a 406 is about the Accept header

    def test_the_log_keeps_the_request_id_and_the_reason(self):
        with self._log() as logs:
            APIClient().get("/api/v1/jobs/nope/", HTTP_ACCEPT="application/xml")
        record = logs.records[0]
        self.assertTrue(record.request_id)
        self.assertIn("Could not satisfy the request Accept header", record.getMessage())

    def test_the_query_string_and_the_body_are_not_logged(self):
        with self._log() as logs:
            APIClient().post(
                "/api/v1/extract/?raw=exact&token=secret",
                {"file": _file(), "object_identifier": "private-id", "object_identifier_type": "UUID"},
                format="multipart",
                HTTP_ACCEPT="application/xml",
            )
        message = logs.records[0].getMessage()
        self.assertNotIn("secret", message)
        self.assertNotIn("private-id", message)
        self.assertNotIn("?", message.split("Could not")[0])  # the path without its query string

    def test_an_overlong_accept_value_is_cut_at_the_limit(self):
        for length, expected_cut in ((200, False), (201, True), (5000, True)):
            accept = "application/x" + "y" * (length - len("application/x"))
            with self.subTest(length=length), self._log() as logs:
                APIClient().get("/api/v1/tools/", HTTP_ACCEPT=accept)
            message = logs.records[0].getMessage()
            shown = message.split("[Accept: ")[1].split("]")[0]
            self.assertEqual(len(shown), 200 if expected_cut else length)

    def test_control_characters_in_the_accept_value_are_escaped(self):
        with self._log() as logs:
            APIClient().get("/api/v1/tools/", HTTP_ACCEPT="application/x\tz\x07")
        message = logs.records[0].getMessage()
        self.assertIn("application/x\\tz\\x07", message)
        self.assertNotIn("\x07", message)
        self.assertNotIn("\n", message)
