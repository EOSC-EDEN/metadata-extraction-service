from django.test import TestCase, override_settings


class LimitsViewTests(TestCase):
    def _limits(self):
        response = self.client.get("/api/v1/limits/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    @override_settings(MES_MAX_UPLOAD_BYTES=1048576, MES_MAX_FILES_PER_REQUEST=7)
    def test_returns_configured_values(self):
        self.assertEqual(
            self._limits(),
            {"max_upload_bytes": 1048576, "max_files_per_request": 7, "async_enabled": True},
        )

    def test_reports_whether_async_is_enabled(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), override_settings(MES_ASYNC_ENABLED=enabled):
                self.assertIs(self._limits()["async_enabled"], enabled)

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post("/api/v1/limits/").status_code, 405)
