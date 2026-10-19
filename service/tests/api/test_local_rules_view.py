import os
import tempfile

from django.test import TestCase, override_settings

URL = "/api/v1/local-rules/"


class LocalRulesViewTests(TestCase):
    def use_rules(self, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        override = override_settings(MES_TOOL_RULES_FILE=handle.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_no_rules_returns_an_empty_list(self):
        with override_settings(MES_TOOL_RULES_FILE="/nonexistent.yaml"):
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"rules": []})

    def test_lists_normalised_rules_in_file_order(self):
        self.use_rules(
            "rules:\n"
            "- {name: root, extensions: ['.ROOT'], tools: [exiftool]}\n"
            "- {name: jpeg, puids: [FMT/43], extensions: [jpg], tools: [mediainfo, exiftool], match: all}\n"
        )
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["rules"],
            [
                {"name": "root", "puids": [], "extensions": ["root"], "tools": ["exiftool"], "match": "any"},
                {
                    "name": "jpeg",
                    "puids": ["fmt/43"],
                    "extensions": ["jpg"],
                    "tools": ["mediainfo", "exiftool"],
                    "match": "all",
                },
            ],
        )

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(URL).status_code, 405)
