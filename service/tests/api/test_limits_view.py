from django.test import TestCase, override_settings


class LimitsViewTests(TestCase):
    def _limits(self):
        response = self.client.get("/api/v1/limits/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    @override_settings(MES_MAX_UPLOAD_BYTES=1048576)
    def test_returns_configured_values(self):
        self.assertEqual(self._limits(), {"max_upload_bytes": 1048576})

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post("/api/v1/limits/").status_code, 405)
