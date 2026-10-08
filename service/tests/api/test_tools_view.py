from django.test import TestCase, override_settings

TOOLS = {
    "alpha": {
        "adapter": "apps.extraction.adapters.exiftool.ExifToolAdapter",
        "kind": "subprocess",
        "invocation_command": "/secret/bin/alpha",
        "extra_args": ["--secret-flag"],
        "timeout_seconds": 12,
        "mandatory": True,
    },
    "beta": {
        "adapter": "apps.extraction.adapters.mediainfo.MediaInfoAdapter",
        "kind": "http",
        "invocation_command": "http://internal.example/beta",
        "enabled": False,
    },
}


@override_settings(MES_TOOLS=TOOLS, MES_DEFAULT_TOOL="alpha")
class ToolsViewTests(TestCase):
    def _tools(self):
        response = self.client.get("/api/v1/tools/")
        self.assertEqual(response.status_code, 200)
        return response.json()["tools"]

    def test_lists_exactly_the_configured_tools(self):
        self.assertEqual([t["name"] for t in self._tools()], ["alpha", "beta"])

    def test_flags_default_mandatory_and_disabled(self):
        alpha, beta = self._tools()
        self.assertEqual(
            alpha,
            {
                "name": "alpha",
                "kind": "subprocess",
                "enabled": True,
                "mandatory": True,
                "default": True,
                "timeout_seconds": 12,
            },
        )
        self.assertFalse(beta["enabled"])
        self.assertFalse(beta["default"])
        self.assertFalse(beta["mandatory"])

    def test_does_not_leak_invocation_details(self):
        body = self.client.get("/api/v1/tools/").content.decode()
        for secret in ("/secret/bin", "--secret-flag", "internal.example", "invocation_command"):
            self.assertNotIn(secret, body)

    def test_listed_in_openapi_schema(self):
        import yaml
        from openapi_spec_validator import validate

        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/vnd.oai.openapi")
        schema = yaml.safe_load(response.content)
        validate(schema)
        self.assertIn("/api/v1/tools/", schema["paths"])
        self.assertIn("Tool", schema["components"]["schemas"])
