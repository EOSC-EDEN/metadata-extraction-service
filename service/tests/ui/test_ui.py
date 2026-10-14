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

    def test_page_has_service_info_panel(self):
        html = b"".join(self.client.get("/").streaming_content).decode()
        for element_id in ("info-panel", "limit-text", "tool-chips"):
            self.assertIn(f'id="{element_id}"', html, element_id)
        self.assertIn("/api/v1/limits/", html)
        self.assertIn("/api/v1/tools/", html)

    def test_logo_is_served_as_svg(self):
        response = self.client.get("/logo-eden.svg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post("/").status_code, 405)
