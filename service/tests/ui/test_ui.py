from django.test import SimpleTestCase

# Element ids the FFIS page defines; the MES page must keep the same skeleton.
FFIS_IDS = [
    "drop-zone", "file-input", "browse-link", "spinner", "error", "result",
    "result-header", "format-name", "format-sub", "id-grid", "file-meta",
    "warnings-card", "warnings-body", "tool-breakdown", "try-again", "try-again-btn",
]


class UiTests(SimpleTestCase):
    def test_index_is_served_as_html(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        html = b"".join(response.streaming_content).decode()
        self.assertIn("Metadata Extraction", html)
        self.assertIn("/api/v1/extract/", html)

    def test_page_keeps_the_ffis_skeleton(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        for element_id in FFIS_IDS:
            self.assertIn(f'id="{element_id}"', html, element_id)

    def test_page_has_optional_puid_and_mime_inputs(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        self.assertIn('id="puid-input"', html)
        self.assertIn('id="mime-input"', html)

    def test_page_has_object_identifier_inputs_and_event_section(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        for element_id in ("object-id-input", "object-id-type-input", "event-section", "event-body"):
            self.assertIn(f'id="{element_id}"', html, element_id)
        self.assertIn("'object_identifier'", html)
        self.assertIn("'object_identifier_type'", html)

    def test_page_has_service_info_panel(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        for element_id in ("info-panel", "limit-text", "tool-chips"):
            self.assertIn(f'id="{element_id}"', html, element_id)
        self.assertIn("/api/v1/limits/", html)
        self.assertIn("/api/v1/tools/", html)

    def test_page_has_async_option_and_jobs_endpoint(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        self.assertIn('id="async-checkbox"', html)
        self.assertIn("/api/v1/jobs/", html)
        # Hidden until the service reports that async processing is enabled.
        self.assertRegex(html, r'<label class="async-option"[^>]*style="display:\s*none')
        self.assertIn("async_enabled", html)
        # The synchronous flow is untouched.
        self.assertIn("/api/v1/extract/", html)

    def _html(self):
        return b"".join(self.client.get("/").streaming_content).decode()

    def test_file_input_accepts_several_files(self):
        import re

        tag = re.search(r'<input[^>]*id="file-input"[^>]*>', self._html()).group(0)
        self.assertRegex(tag, r"\bmultiple\b")

    def test_page_has_the_batch_result_container_and_routes(self):
        html = self._html()
        self.assertIn('id="batch-results"', html)
        self.assertIn("batch-group-", html)
        self.assertIn("/api/v1/extract/batch/", html)
        self.assertIn("/api/v1/jobs/batch/", html)
        self.assertIn("/api/v1/job-batches/", html)
        self.assertIn("max_files_per_request", html)

    def test_batch_sends_the_files_field_once_per_file_and_no_object_identifier(self):
        html = self._html()
        self.assertIn("form.append('files', f)", html)
        # The single-file flow still sends `file`, and the object identifier stays single-file only.
        self.assertIn("form.append('file', file)", html)
        self.assertIn("object identifier cannot be used with several files", html)

    def test_single_file_flow_and_ffis_ids_are_unchanged(self):
        html = self._html()
        self.assertIn("fetchJson('/api/v1/extract/', { method: 'POST', body: form })", html)
        self.assertIn("extractAsync(form)", html)
        for element_id in FFIS_IDS:
            self.assertEqual(html.count(f'id="{element_id}"'), 1, element_id)

    def test_result_groups_are_built_from_a_template_without_duplicate_ids(self):
        html = self._html()
        # Group markup is cloned from the pristine #result and its ids moved to data-role,
        # so the page never has two elements with the same id.
        self.assertIn("RESULT_TEMPLATE", html)
        self.assertIn("data-role", html)
        self.assertIn("removeAttribute('id')", html)

    def test_batch_content_is_escaped(self):
        html = self._html()
        self.assertIn("title.textContent = name", html)
        self.assertNotIn("innerHTML = `<h2", html)

    def test_logo_is_served_as_svg(self):
        response = self.client.get("/logo-eden.svg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post("/").status_code, 405)
