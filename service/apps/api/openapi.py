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
                            "object_identifier": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional identifier of the file's own PREMIS object (for example its path in the SIP). Echoed in the extraction event's `linkingObjectIdentifier`; must be given together with `object_identifier_type`. Without both, the event links the uploaded filename.",
                            },
                            "object_identifier_type": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Type of `object_identifier` (for example `filename` or `sip-path`). Must be given together with `object_identifier`.",
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


class LimitsAutoSchema(AutoSchema):
    """Describes the read-only deployment limits."""

    def get_operation_id(self, path, method):
        return "getLimits"

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "Deployment limits.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["max_upload_bytes", "async_enabled"],
                            "properties": {
                                "max_upload_bytes": {
                                    "type": "integer",
                                    "description": "Largest accepted upload; larger files get HTTP 413.",
                                },
                                "async_enabled": {
                                    "type": "boolean",
                                    "description": "Whether the asynchronous `/jobs/` endpoints are enabled (`MES_ASYNC_ENABLED`); when false they return 404.",
                                },
                            },
                        }
                    }
                },
            }
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


def load_job_schema() -> dict:
    return _to_openapi(json.loads((SCHEMA_DIR / "mes-job.schema.json").read_text()))


_JOB_ID_PARAMETER = {
    "name": "job_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "The `job_id` returned when the job was submitted.",
}


def _job_error(description):
    return {
        "description": description,
        "content": {"application/json": {"schema": deepcopy(_ERRORS_SCHEMA)}},
    }


class JobSubmitAutoSchema(ExtractAutoSchema):
    """Same multipart body as `/extract/`; responds 202 with the job envelope."""

    def get_operation_id(self, path, method):
        return "submitJob"

    def get_responses(self, path, method):
        return {
            "202": {
                "description": "Job accepted. `Location` points at the job's status resource.",
                "headers": {"Location": {"schema": {"type": "string"}}},
                "content": _json_and_xml(load_job_schema()),
            },
            "400": _job_error("Malformed request, e.g. missing or empty `file`."),
            "404": _job_error("Asynchronous processing is disabled (`MES_ASYNC_ENABLED`)."),
            "413": _job_error("The uploaded file exceeds the configured maximum size (`MES_MAX_UPLOAD_BYTES`)."),
            "500": _job_error("Unexpected error while processing the request."),
            "503": _job_error("The job store is full (`MES_ASYNC_MAX_JOBS`); try again later."),
        }


class JobStatusAutoSchema(AutoSchema):
    def get_operation_id(self, path, method):
        return "getJob"

    def get_path_parameters(self, path, method):
        return [deepcopy(_JOB_ID_PARAMETER)]

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "The job envelope (JSON, or XML with `Accept: application/xml`).",
                "content": _json_and_xml(load_job_schema()),
            },
            "404": _job_error("Unknown or expired job, or asynchronous processing is disabled."),
        }


class JobResultAutoSchema(AutoSchema):
    def get_operation_id(self, path, method):
        return "getJobResult"

    def get_path_parameters(self, path, method):
        return [deepcopy(_JOB_ID_PARAMETER)]

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "Exactly the `/extract/` result (JSON, or XML with `Accept: application/xml`).",
                "content": _json_and_xml(load_result_schema()),
            },
            "404": _job_error("Unknown or expired job, or asynchronous processing is disabled."),
            "409": _job_error("The job has not finished, or it failed and so has no result."),
        }
