from .base import HTTPToolAdapter

_CHUNK_SIZE = 64 * 1024


def _stream_file(file_path):
    """Yield the file in chunks, closing it when the request is done (no whole-file read)."""
    with open(file_path, "rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            yield chunk


class TikaAdapter(HTTPToolAdapter):
    """Apache Tika server (externally hosted, MES-REQ-1-005); `/rmeta` returns metadata as JSON."""

    tool_name = "tika"
    raw_output_format = "json"

    def build_request(self, file_path) -> dict:
        return {
            "method": "PUT",
            "url": f"{self.base_url.rstrip('/')}/rmeta",
            "headers": {"Accept": "application/json"},
            "data": _stream_file(file_path),
        }
