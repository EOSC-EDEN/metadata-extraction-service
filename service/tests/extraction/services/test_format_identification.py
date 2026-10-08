import tempfile
from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.extraction.exceptions import FFISUnavailable
from apps.extraction.services.format_identification import FFISClient


def _response(payload=None, status_ok=True, json_error=False):
    response = mock.Mock()
    if status_ok:
        response.raise_for_status.return_value = None
    else:
        response.raise_for_status.side_effect = requests.HTTPError("500")
    if json_error:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = payload
    return response


def _payload(outcome="success", identifiers=None, **extra):
    return {
        "identifiers": identifiers
        if identifiers is not None
        else [
            {"value": "fmt/43", "scheme": "PRONOM", "uri": None},
            {"value": "image/jpeg", "scheme": "mime", "uri": None},
        ],
        "primary_identifier": None,
        "provenance": {"outcome": outcome, "tools": extra.pop("tools", [])},
        "warnings": extra.pop("warnings", []),
        **extra,
    }


class FFISClientTests(SimpleTestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".jpg")
        self.addCleanup(self.file.close)
        self.client = FFISClient(base_url="http://ffis.test/", timeout_seconds=5)

    def _identify(self, response=None, side_effect=None):
        with mock.patch(
            "apps.extraction.services.format_identification.requests.post",
            return_value=response,
            side_effect=side_effect,
        ) as post:
            try:
                return self.client.identify(self.file.name), post
            finally:
                self.post = post

    def test_success_extracts_puid_and_mime(self):
        resolution, post = self._identify(_response(_payload()))

        self.assertEqual((resolution.puid, resolution.mime_type), ("fmt/43", "image/jpeg"))
        self.assertEqual(resolution.outcome, "success")
        self.assertEqual(post.call_args.args[0], "http://ffis.test/identify")
        self.assertEqual(post.call_args.kwargs["timeout"], 5)
        self.assertIn("file", post.call_args.kwargs["files"])

    def test_warning_outcome_is_usable_and_surfaces_warnings(self):
        payload = _payload(
            outcome="warning",
            warnings=[{"message": "engines disagree"}],
            tools=[{"name": "magika", "error": "model failed"}],
        )

        resolution, _ = self._identify(_response(payload))

        self.assertEqual(resolution.puid, "fmt/43")
        self.assertEqual(
            resolution.engine_warnings,
            ["engines disagree", "FFIS engine 'magika': model failed"],
        )

    def test_failure_outcome_is_inconclusive(self):
        resolution, _ = self._identify(_response(_payload(outcome="failure")))
        self.assertIsNone(resolution)

    def test_empty_identifiers_is_inconclusive(self):
        resolution, _ = self._identify(_response(_payload(identifiers=[])))
        self.assertIsNone(resolution)

    def test_falls_back_to_primary_identifier_when_no_scheme_matches(self):
        payload = _payload(identifiers=[{"value": "x", "scheme": "other"}])
        payload["primary_identifier"] = {"value": "image/png", "scheme": "other"}

        resolution, _ = self._identify(_response(payload))

        self.assertEqual((resolution.puid, resolution.mime_type), (None, "image/png"))

    def test_network_error_raises_unavailable(self):
        with self.assertRaises(FFISUnavailable):
            self._identify(side_effect=requests.ConnectionError("down"))

    def test_http_error_raises_unavailable(self):
        with self.assertRaises(FFISUnavailable):
            self._identify(_response(status_ok=False))

    def test_non_json_raises_unavailable(self):
        with self.assertRaises(FFISUnavailable):
            self._identify(_response(json_error=True))

    def test_malformed_payload_raises_unavailable(self):
        with self.assertRaises(FFISUnavailable):
            self._identify(_response(["not", "an", "object"]))
        with self.assertRaises(FFISUnavailable):
            self._identify(_response(_payload(identifiers=["bad"])))

    def test_missing_base_url_raises_unavailable(self):
        with self.assertRaises(FFISUnavailable):
            FFISClient(base_url="").identify(self.file.name)
