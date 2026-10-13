from django.test import TestCase


class MetricsViewTests(TestCase):
    def test_metrics_endpoint_returns_prometheus_text(self):
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        body = response.content.decode()
        self.assertIn("mes_extract_requests_total", body)
        self.assertIn("mes_uploaded_bytes_total", body)

    def test_metrics_endpoint_is_get_only(self):
        self.assertEqual(self.client.post("/metrics").status_code, 405)
