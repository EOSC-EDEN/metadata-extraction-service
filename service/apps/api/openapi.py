"""OpenAPI 3 description of the extract endpoint (DRF's built-in generator).

The 200 response body is the project's own JSON Schema
(`schema/mes-result.schema.json`), so the API docs and the validated result
shape cannot drift apart.
"""

import json
from copy import deepcopy
from pathlib import Path

from rest_framework.schemas.openapi import AutoSchema

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

_ERRORS_SCHEMA = {
    "type": "object",
    "required": ["errors"],
    "properties": {
        "errors": {"type": "array", "items": {"type": "string"}},
        "request_id": {"type": "string", "description": "Matches the id in the service logs."},
    },
}


def _to_openapi(node):
    """Rewrite JSON Schema 2020-12 idioms that OpenAPI 3.0 does not accept."""
    if isinstance(node, list):
        return [_to_openapi(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("$schema", "$id"):
            continue  # belong to the standalone document, not an OpenAPI component
        out[key] = _to_openapi(value)
    types = out.get("type")
    if isinstance(types, list):
        non_null = [t for t in types if t != "null"]
        if len(non_null) != len(types):
            out["nullable"] = True
        out["type"] = non_null[0]
    return out


def load_result_schema() -> dict:
    return _to_openapi(json.loads((SCHEMA_DIR / "mes-result.schema.json").read_text()))


def _json_and_xml(schema):
    return {
        "application/json": {"schema": deepcopy(schema)},
        "application/xml": {"schema": {"type": "string", "format": "xml"}},
    }


class ExtractAutoSchema(AutoSchema):
    """Describes the multipart request and the result/error responses."""

    def get_operation_id(self, path, method):
        return "extractMetadata"

    def get_request_body(self, path, method):
        return {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "The single file to extract metadata from.",
                            },
                            "puid": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Optional PRONOM PUID of the file. If given, format identification is not called.",
                            },
                            "mime_type": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional MIME type of the file. If given, format identification is not called.",
                            },
                        },
                    }
                }
            },
        }

    def get_responses(self, path, method):
        def error(description):
            return {
                "description": description,
                "content": {"application/json": {"schema": deepcopy(_ERRORS_SCHEMA)}},
            }

        return {
            "200": {
                "description": "Aggregated extraction result (JSON, or XML with `Accept: application/xml`).",
                "content": _json_and_xml(load_result_schema()),
            },
            "400": error("Malformed request, e.g. missing or empty `file`."),
            "413": error("The uploaded file exceeds the configured maximum size (`MES_MAX_UPLOAD_BYTES`)."),
            "500": error("Unexpected error while processing the request."),
            "503": error("No default extraction tool is configured."),
        }


class ToolsAutoSchema(AutoSchema):
    """Describes the read-only tool listing."""

    def get_operation_id(self, path, method):
        return "listTools"

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "The tools configured for metadata extraction.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["tools"],
                            "properties": {
                                "tools": {"type": "array", "items": {"$ref": "#/components/schemas/Tool"}}
                            },
                        }
                    }
                },
            }
        }

    def get_components(self, path, method):
        return {
            "Tool": {
                "type": "object",
                "required": ["name", "kind", "enabled", "mandatory", "default", "timeout_seconds"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["subprocess", "http"]},
                    "enabled": {"type": "boolean"},
                    "mandatory": {"type": "boolean", "description": "Failure surfaces a top-level error (MES-REQ-3-006)."},
                    "default": {"type": "boolean", "description": "Whether this is the default tool (MES-REQ-1-001)."},
                    "timeout_seconds": {"type": "integer"},
                },
            }
        }
