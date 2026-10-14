import os
import tempfile
from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.tika import TikaAdapter

REQUEST = "apps.extraction.adapters.base.requests.request"


def _response(text="", status_code=200):
    response = requests.Response()
    response.status_code = status_code
    response._content = text.encode()
    response.url = "http://tika:9998/rmeta"
    return response


class TikaAdapterTests(SimpleTestCase):
    def _file(self, content=b"hello"):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _adapter(self, base_url="http://tika:9998"):
        return TikaAdapter(base_url=base_url, timeout_seconds=5)

    def test_build_request_is_a_streamed_put_to_rmeta(self):
        path = self._file(b"a" * (64 * 1024 + 1))
        request = self._adapter("http://tika:9998/").build_request(path)
        self.assertEqual(request["method"], "PUT")
        self.assertEqual(request["url"], "http://tika:9998/rmeta")
        self.assertEqual(request["headers"], {"Accept": "application/json"})
        chunks = list(request["data"])
        self.assertEqual(len(chunks), 2)  # chunked, not one whole-file read
        self.assertEqual(b"".join(chunks), b"a" * (64 * 1024 + 1))

    @mock.patch(REQUEST)
    def test_success_returns_raw_json(self, mock_request):
        mock_request.return_value = _response('[{"Content-Type":"text/plain"}]')
        result = self._adapter().run(self._file())
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, '[{"Content-Type":"text/plain"}]')
        self.assertEqual(result.raw_output_format, "json")
        self.assertEqual(result.tool_name, "tika")
        self.assertEqual(result.command, ("PUT", "http://tika:9998/rmeta"))
        self.assertEqual(mock_request.call_args.kwargs["timeout"], 5)

    @mock.patch(REQUEST)
    def test_server_error_maps_to_failure(self, mock_request):
        mock_request.return_value = _response("boom", status_code=500)
        self.assertEqual(self._adapter().run(self._file()).status, ToolExecutionStatus.FAILURE)

    @mock.patch(REQUEST)
    def test_unsupported_media_type_maps_to_failure(self, mock_request):
        mock_request.return_value = _response("", status_code=422)
        self.assertEqual(self._adapter().run(self._file()).status, ToolExecutionStatus.FAILURE)

    @mock.patch(REQUEST)
    def test_timeout_maps_to_timeout(self, mock_request):
        mock_request.side_effect = requests.exceptions.ReadTimeout("slow")
        self.assertEqual(self._adapter().run(self._file()).status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(REQUEST)
    def test_connection_error_maps_to_failure(self, mock_request):
        mock_request.side_effect = requests.exceptions.ConnectionError("refused")
        result = self._adapter().run(self._file())
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIn("refused", result.errors[0])
