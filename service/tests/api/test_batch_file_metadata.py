"""Per-file `puid`, `mime_type` and object identifier on the batch route (`file_metadata`)."""

import hashlib
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile

from tests.api import test_jobs_batch_view as async_tests
from tests.api.test_jobs_batch_view import _ok, _upload

UUID = "3f1c9a52-7d0e-4b8a-9a52-0c1e2f3a4b5c"


def _link(result):
    (link,) = result["provenance"]["object"]["objectIdentifier"]
    return link["objectIdentifierType"], link["objectIdentifierValue"]


def _designations(result):
    formats = result["provenance"]["object"]["objectCharacteristics"][0]["format"]
    return [f.get("formatDesignation") for f in formats]


def _characteristics(result):
    return result["provenance"]["object"]["objectCharacteristics"][0]


def _resolution(result):
    resolution = result["tool_resolution"]
    return resolution["puid"], resolution["mime_type"]


class FileMetadataCases:
    """Behaviour of the batch route; a subclass says how to submit and read."""

    def submit(self, files, manifest=None, **fields):
        if manifest is not None:
            fields["file_metadata"] = manifest if isinstance(manifest, str) else json.dumps(manifest)
        return self._submit(files, **fields)

    def assertRejected(self, response, *fragments):
        self.assertEqual(response.status_code, 400)
        errors = " ".join(response.json()["errors"])
        for fragment in fragments:
            self.assertIn(fragment, errors)

    def test_an_entry_applies_to_the_file_with_that_name_only(self, run):
        response = self.submit(
            [_upload(name="a.jpg"), _upload(name="b.jpg")], {"a.jpg": {"puid": "fmt/43"}}
        )

        a, b = self.results(response)
        self.assertEqual(_resolution(a), ("fmt/43", "image/jpeg"))
        self.assertEqual(_resolution(b), (None, "image/jpeg"))

    def test_a_per_file_value_overrides_the_request_level_default(self, run):
        response = self.submit(
            [_upload(name="a.jpg"), _upload(name="b.pdf"), _upload(name="c.jpg")],
            {"b.pdf": {"mime_type": "application/pdf"}},
        )

        a, b, c = self.results(response)
        self.assertEqual([_resolution(r)[1] for r in (a, b, c)], ["image/jpeg", "application/pdf", "image/jpeg"])

    def test_the_request_level_puid_stays_the_default_for_other_files(self, run):
        response = self.submit(
            [_upload(name="a.jpg"), _upload(name="b.jpg")], {"a.jpg": {"puid": "fmt/18"}}, puid="fmt/43"
        )

        a, b = self.results(response)
        self.assertEqual([_resolution(r)[0] for r in (a, b)], ["fmt/18", "fmt/43"])

    def test_a_blank_per_file_value_falls_back_to_the_default(self, run):
        response = self.submit([_upload(name="a.jpg")], {"a.jpg": {"puid": ""}}, puid="fmt/43")

        (a,) = self.results(response)
        self.assertEqual(_resolution(a)[0], "fmt/43")

    def test_the_event_links_the_object_identifier_of_that_file_only(self, run):
        response = self.submit(
            [_upload(name="a.jpg"), _upload(name="b.jpg")],
            {"a.jpg": {"object_identifier": UUID, "object_identifier_type": "UUID"}},
        )

        a, b = self.results(response)
        self.assertEqual(_link(a), ("UUID", UUID))
        self.assertEqual(_link(b), ("filepath", "b.jpg"))

    def test_the_order_of_uploads_and_keys_does_not_matter(self, run):
        manifest = {"b.jpg": {"puid": "fmt/18"}, "a.jpg": {"puid": "fmt/43"}}

        response = self.submit([_upload(name="a.jpg"), _upload(name="b.jpg")], manifest)
        reversed_response = self.submit([_upload(name="b.jpg"), _upload(name="a.jpg")], manifest)

        a, b = self.results(response)
        rb, ra = self.results(reversed_response)
        self.assertEqual((_resolution(a)[0], _resolution(b)[0]), ("fmt/43", "fmt/18"))
        self.assertEqual((_resolution(ra)[0], _resolution(rb)[0]), ("fmt/43", "fmt/18"))

    def test_null_and_empty_entries_mean_nothing_for_that_file(self, run):
        response = self.submit([_upload(name="a.jpg"), _upload(name="b.jpg")], {"a.jpg": None, "b.jpg": {}})

        self.assertEqual(response.status_code, self.ok_status)

    def test_an_empty_or_blank_manifest_behaves_like_none(self, run):
        for manifest in ("", "{}"):
            with self.subTest(manifest=manifest):
                response = self.submit([_upload()], manifest)
                self.assertEqual(response.status_code, self.ok_status)

    def test_format_names_follow_the_same_default_and_override_rules(self, run):
        response = self.submit(
            [_upload(name="a.jpg"), _upload(name="b.jpg"), _upload(name="c.jpg")],
            {"b.jpg": {"format_name": "Own name", "media_type_name": "own"}, "c.jpg": None},
            puid="fmt/43",
            format_name="Shared name",
            format_version="1.01",
        )

        a, b, c = self.results(response)
        self.assertEqual(_designations(a), [{"formatName": "Shared name", "formatVersion": "1.01"}, None])
        # the file's own name replaces the shared one; the shared version still serves it
        self.assertEqual(
            _designations(b), [{"formatName": "Own name", "formatVersion": "1.01"}, {"formatName": "own"}]
        )
        self.assertEqual(_designations(c), _designations(a))

    def test_an_overlong_per_file_format_name_is_rejected(self, run):
        for field, length in (("format_name", 256), ("format_version", 65), ("media_type_name", 256)):
            response = self.submit([_upload(name="a.jpg")], {"a.jpg": {field: "x" * length}})
            self.assertRejected(response, "a.jpg", field)
        self.assertNothingStarted(run)

    def test_each_object_has_the_size_and_sha256_of_its_own_file(self, run):
        response = self.submit([_upload(b"one", "a.jpg"), _upload(b"x", "b.jpg"), _upload(b"twelve bytes", "c.jpg")])

        for result, content in zip(self.results(response), (b"one", b"x", b"twelve bytes")):
            characteristics = _characteristics(result)
            self.assertEqual(characteristics["size"], len(content))
            (fixity,) = characteristics["fixity"]
            self.assertEqual(fixity["messageDigestAlgorithm"], "SHA-256")
            self.assertEqual(fixity["messageDigest"], hashlib.sha256(content).hexdigest())
            self.assertEqual(fixity["messageDigestOriginator"], "Metadata Extraction Service")
            self.assertEqual(list(characteristics), ["fixity", "size", "format"])

    def test_no_manifest_behaves_as_before(self, run):
        response = self.submit([_upload(name="a.jpg")], puid="fmt/43")

        (a,) = self.results(response)
        self.assertEqual(_resolution(a), ("fmt/43", "image/jpeg"))
        self.assertEqual(_link(a), ("filepath", "a.jpg"))

    def test_duplicate_filenames_without_an_entry_are_still_accepted(self, run):
        response = self.submit([_upload(b"one", "same.jpg"), _upload(b"two", "same.jpg")])

        self.assertEqual(response.status_code, self.ok_status)

    # --- rejected requests -------------------------------------------------------------------

    def test_a_key_that_matches_no_file_is_rejected(self, run):
        response = self.submit([_upload(name="a.jpg")], {"typo.jpg": {"puid": "fmt/43"}})

        self.assertRejected(response, "file_metadata", "typo.jpg")
        self.assertNothingStarted(run)

    def test_a_key_that_matches_two_files_is_rejected(self, run):
        response = self.submit(
            [_upload(b"one", "same.jpg"), _upload(b"two", "same.jpg")], {"same.jpg": {"puid": "fmt/43"}}
        )

        self.assertRejected(response, "same.jpg")
        self.assertNothingStarted(run)

    def test_invalid_json_is_rejected(self, run):
        self.assertRejected(self.submit([_upload()], "{not json"), "file_metadata")
        self.assertNothingStarted(run)

    def test_a_manifest_that_is_not_an_object_is_rejected(self, run):
        for manifest in ("[]", '"x"', "3", "true"):
            with self.subTest(manifest=manifest):
                self.assertRejected(self.submit([_upload()], manifest), "file_metadata")

    def test_an_entry_that_is_not_an_object_is_rejected(self, run):
        for value in ("fmt/43", 3, ["puid"], True):
            with self.subTest(value=value):
                self.assertRejected(self.submit([_upload(name="a.jpg")], {"a.jpg": value}), "a.jpg")

    def test_an_unknown_field_in_an_entry_is_rejected(self, run):
        response = self.submit([_upload(name="a.jpg")], {"a.jpg": {"pui": "fmt/43"}})

        self.assertRejected(response, "a.jpg", "pui")

    def test_an_identifier_without_its_type_is_rejected_and_the_reverse(self, run):
        for entry in ({"object_identifier": UUID}, {"object_identifier_type": "UUID"}):
            with self.subTest(entry=entry):
                response = self.submit([_upload(name="a.jpg")], {"a.jpg": entry})
                self.assertRejected(response, "a.jpg", "object_identifier", "object_identifier_type")

    def test_a_blank_identifier_pair_counts_as_not_given(self, run):
        response = self.submit(
            [_upload(name="a.jpg")], {"a.jpg": {"object_identifier": "", "object_identifier_type": ""}}
        )

        self.assertEqual(response.status_code, self.ok_status)

    def test_length_boundaries(self, run):
        cases = [
            ("puid", 64, 65),
            ("mime_type", 255, 256),
            ("object_identifier", 255, 256),
            ("object_identifier_type", 64, 65),
        ]
        for field, ok_length, bad_length in cases:
            extra = {"object_identifier_type": "T"} if field == "object_identifier" else {}
            if field == "object_identifier_type":
                extra = {"object_identifier": "v"}
            with self.subTest(field=field, length=ok_length):
                entry = {field: "x" * ok_length, **extra}
                self.assertEqual(self.submit([_upload(name="a.jpg")], {"a.jpg": entry}).status_code, self.ok_status)
            with self.subTest(field=field, length=bad_length):
                entry = {field: "x" * bad_length, **extra}
                self.assertRejected(self.submit([_upload(name="a.jpg")], {"a.jpg": entry}), "a.jpg")

    def test_a_request_level_object_identifier_is_still_rejected(self, run):
        self.assertRejected(
            self.submit([_upload(name="a.jpg")], {"a.jpg": {"puid": "fmt/43"}}, object_identifier="abc"),
            "object_identifier",
        )


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=async_tests._ok)
class AsyncFileMetadataTests(FileMetadataCases, async_tests.BatchJobsTestCase):
    ok_status = 202

    def _submit(self, files, **fields):
        return self.submit_batch(files, **fields)

    def results(self, response):
        self.assertEqual(response.status_code, 202)
        jobs = response.json()["jobs"]
        return [self.client.get(f"/api/v1/jobs/{job['job_id']}/result/").json() for job in jobs]

    def assertNothingStarted(self, run):
        run.assert_not_called()
        self.assertNoUploadsLeft()

    def test_the_job_result_equals_the_sync_result_for_the_same_metadata(self, run):
        manifest = {"a.jpg": {"puid": "fmt/43", "object_identifier": UUID, "object_identifier_type": "UUID"}}

        (job_result,) = self.results(self.submit([_upload(name="a.jpg")], manifest))
        sync = self.client.post(
            "/api/v1/extract/",
            {
                "file": _upload(name="a.jpg"),
                "puid": "fmt/43",
                "mime_type": "image/jpeg",
                "object_identifier": UUID,
                "object_identifier_type": "UUID",
            },
            format="multipart",
        ).json()

        self.assertEqual(async_tests._stable(job_result), async_tests._stable(sync))
        self.assertEqual(_link(job_result), _link(sync))

    def test_a_rejected_manifest_creates_no_job(self, run):
        response = self.submit([_upload(name="a.jpg")], {"typo.jpg": {}})

        self.assertEqual(response.status_code, 400)
        self.assertNoUploadsLeft()
