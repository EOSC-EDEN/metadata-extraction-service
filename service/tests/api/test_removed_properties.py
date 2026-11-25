"""`format_name`, `format_version` and `media_type_name` are no longer request properties.

The service keeps `puid`, `mime_type`, `object_identifier` and `object_identifier_type`. The names of the
formats had no source, so a request that still sends one gets a 400.
"""

import json
import subprocess
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

REMOVED = ("format_name", "format_version", "media_type_name")
ROUTES = (("/api/v1/extract/", "file"), ("/api/v1/jobs/", "file"), ("/api/v1/jobs/batch/", "files"))


def _file(name="a.jpg"):
    return SimpleUploadedFile(name, b"data", content_type="image/jpeg")


class RemovedPropertiesTests(TestCase):
    def setUp(self):
        # a real CompletedProcess, as in the other tests: a bare Mock would make a tool run work on a Mock
        patcher = mock.patch(
            "apps.extraction.adapters.base.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="<a/>", stderr=""),
        )
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, route, field, **extra):
        return APIClient().post(route, {field: _file(), **extra}, format="multipart")

    def test_each_removed_property_is_a_400_that_says_so_on_every_upload_route(self):
        for route, field in ROUTES:
            for name in REMOVED:
                with self.subTest(route=route, name=name):
                    response = self._post(route, field, **{name: "x"})
                    self.assertEqual(response.status_code, 400)
                    errors = response.json()["errors"]
                    self.assertTrue(any(name in e and "no longer accepted" in e.lower() for e in errors), errors)

    def test_a_rejected_request_runs_nothing(self):
        for route, field in ROUTES:
            self._post(route, field, format_name="JPEG")
        self.run.assert_not_called()

    def test_blank_values_still_count_as_not_given(self):
        # multipart forms send empty fields; a blank value is not a use of the property
        response = self._post("/api/v1/extract/", "file", format_name="", format_version="", media_type_name="")
        self.assertEqual(response.status_code, 200)

    def test_the_kept_properties_are_still_accepted(self):
        response = self._post(
            "/api/v1/extract/", "file", puid="fmt/43", mime_type="image/jpeg", object_identifier="id-1", object_identifier_type="UUID"
        )
        self.assertEqual(response.status_code, 200)
        link = response.json()["provenance"]["object"]["objectIdentifier"][0]
        self.assertEqual((link["objectIdentifierType"], link["objectIdentifierValue"]), ("UUID", "id-1"))

    def test_the_removed_properties_are_unknown_in_the_per_file_manifest(self):
        for name in REMOVED:
            with self.subTest(name=name):
                manifest = json.dumps({"a.jpg": {name: "x"}})
                response = self._post("/api/v1/jobs/batch/", "files", file_metadata=manifest)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(any(name in e for e in response.json()["errors"]))
        self.run.assert_not_called()

    def test_the_request_level_removed_properties_are_rejected_on_the_batch_route_too(self):
        response = APIClient().post(
            "/api/v1/jobs/batch/", {"files": [_file("a.jpg"), _file("b.jpg")], "format_name": "JPEG"}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)


class CallerHintsSkipIdentificationTests(TestCase):
    """FFIS is called unless the caller gave a `puid` or a `mime_type`."""

    class Fake:
        base_url = "http://ffis.test"

        def __init__(self):
            self.calls = 0

        def identify(self, path):
            from apps.extraction.services.format_identification import FFISResolution

            self.calls += 1
            return FFISResolution("fmt/43", "image/jpeg", "ok", [], {"outcome": "ok"})

    @override_settings(MES_FFIS_ENABLED=True, MES_TOOL_RULES_FILE="/nonexistent/rules.yaml")
    def test_ffis_is_called_without_a_hint_and_skipped_with_a_puid_or_a_mime_type(self):
        from apps.extraction.services.tool_resolution import resolve_tools

        fake = self.Fake()
        identified = resolve_tools("/f", ffis_client=fake)
        self.assertEqual((fake.calls, identified.puid, identified.mime_type), (1, "fmt/43", "image/jpeg"))

        for hint, expected in (({"puid": "fmt/18"}, ("fmt/18", None)), ({"mime_type": "audio/flac"}, (None, "audio/flac"))):
            with self.subTest(hint=hint):
                fake = self.Fake()
                given = resolve_tools("/f", ffis_client=fake, **hint)
                self.assertEqual((fake.calls, given.puid, given.mime_type), (0, *expected))
                self.assertEqual(given.selection_method, "default_tool_caller_identified")
