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


def _json_and_premis(schema):
    """JSON and the PREMIS document (`application/premis+xml`); `application/xml` is not offered (406)."""
    return {
        "application/json": {"schema": deepcopy(schema)},
        "application/premis+xml": {"schema": {"type": "string", "format": "xml"}},
    }


def _json_only(schema):
    """An envelope (job, batch of jobs): JSON only, `Accept: application/xml` is answered with 406."""
    return {"application/json": {"schema": deepcopy(schema)}}


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
                            "format_name": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional name of the file's PRONOM format (for example `JPEG File Interchange Format`), shown as the `formatName` of the PRONOM format in `provenance.object`. Never derived: without it the PRONOM format has only its registry key.",
                            },
                            "format_version": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Optional version of the PRONOM format (for example `1.01`). Only used together with `format_name`.",
                            },
                            "media_type_name": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional name of the file's IANA media type (for example `jpeg`), shown as the `formatName` of the media type format.",
                            },
                            "object_identifier": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional identifier of the file's own PREMIS object (for example its path in the SIP). Echoed as the `objectIdentifier` of the PREMIS file object in `provenance.object`; must be given together with `object_identifier_type`. Without both, the object is identified by the uploaded filename, with type `filepath`.",
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
                "description": "Aggregated extraction result (JSON, or its PREMIS part as a standalone `premis:premis` document with `Accept: application/premis+xml`; `Accept: application/xml` is answered with `406`).",
                "content": _json_and_premis(load_result_schema()),
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
                            "required": ["max_upload_bytes", "max_files_per_request", "async_enabled"],
                            "properties": {
                                "max_upload_bytes": {
                                    "type": "integer",
                                    "description": "Largest accepted upload; larger files get HTTP 413.",
                                },
                                "max_files_per_request": {
                                    "type": "integer",
                                    "description": "Most files accepted by one batch request (`MES_MAX_FILES_PER_REQUEST`); more get HTTP 400.",
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


class LocalRulesAutoSchema(AutoSchema):
    """Describes the read-only listing of local format rules."""

    def get_operation_id(self, path, method):
        return "listLocalRules"

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "The local format rules, in file order; empty when none are configured.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["rules"],
                            "properties": {
                                "rules": {"type": "array", "items": {"$ref": "#/components/schemas/LocalRule"}}
                            },
                        }
                    }
                },
            }
        }

    def get_components(self, path, method):
        return {
            "LocalRule": {
                "type": "object",
                "required": ["name", "puids", "extensions", "tools", "match"],
                "properties": {
                    "name": {"type": "string"},
                    "puids": {"type": "array", "items": {"type": "string"}, "description": "PRONOM identifiers, lower-case."},
                    "extensions": {"type": "array", "items": {"type": "string"}, "description": "File extensions, lower-case, no leading dot."},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Tools that run when the rule matches (the default tool does not run when a rule matches)."},
                    "match": {
                        "type": "string",
                        "enum": ["any", "all"],
                        "description": "`any`: PUID or extension matches; `all`: both must match.",
                    },
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
                "content": _json_only(load_job_schema()),
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
                "description": "The job envelope (JSON only; `Accept: application/xml` is answered with `406`).",
                "content": _json_only(load_job_schema()),
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
                "description": "Exactly the `/extract/` result (JSON, or standalone PREMIS with `Accept: application/premis+xml`; `Accept: application/xml` is answered with `406`).",
                "content": _json_and_premis(load_result_schema()),
            },
            "404": _job_error("Unknown or expired job, or asynchronous processing is disabled."),
            "409": _job_error("The job has not finished, or it failed and so has no result."),
        }


def load_job_batch_schema() -> dict:
    return _to_openapi(json.loads((SCHEMA_DIR / "mes-job-batch.schema.json").read_text()))


_BATCH_ID_PARAMETER = {
    "name": "batch_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "The `batch_id` returned when the batch was submitted.",
}


class _BatchRequestBodyMixin:
    def get_request_body(self, path, method):
        return {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["files"],
                        "properties": {
                            "files": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "format": "binary"},
                                "description": (
                                    "The files to extract metadata from: repeat the `files` field once per "
                                    "file. At most `max_files_per_request` files (`GET /limits/`) and "
                                    "`max_upload_bytes` in total."
                                ),
                            },
                            "puid": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Optional PRONOM PUID, the default for every file without its own in `file_metadata`. If given, format identification is not called.",
                            },
                            "mime_type": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional MIME type, the default for every file without its own in `file_metadata`. If given, format identification is not called.",
                            },
                            "format_name": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional PRONOM format name, the default for every file without its own in `file_metadata`.",
                            },
                            "format_version": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Optional PRONOM format version, the default for every file without its own. Only used together with `format_name`.",
                            },
                            "media_type_name": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional name of the IANA media type, the default for every file without its own.",
                            },
                            "file_metadata": {
                                "type": "object",
                                "description": (
                                    "Optional values per file, as a JSON object keyed by uploaded filename. A key "
                                    "must name exactly one uploaded file (an unknown or ambiguous key gives 400). "
                                    "A file's own `puid` and `mime_type` replace the request-level ones; "
                                    "`object_identifier` and `object_identifier_type` must be given together and "
                                    "become the PREMIS object link of that file's event (otherwise its filename)."
                                ),
                                "additionalProperties": {
                                    "type": "object",
                                    "nullable": True,
                                    "additionalProperties": False,
                                    "properties": {
                                        "puid": {"type": "string", "maxLength": 64},
                                        "mime_type": {"type": "string", "maxLength": 255},
                                        "format_name": {"type": "string", "maxLength": 255},
                                        "format_version": {"type": "string", "maxLength": 64},
                                        "media_type_name": {"type": "string", "maxLength": 255},
                                        "object_identifier": {"type": "string", "maxLength": 255},
                                        "object_identifier_type": {"type": "string", "maxLength": 64},
                                    },
                                },
                                "example": {
                                    "a.jpg": {
                                        "puid": "fmt/43",
                                        "object_identifier": "3f1c9a52-7d0e-4b8a-9a52-0c1e2f3a4b5c",
                                        "object_identifier_type": "UUID",
                                    },
                                    "b.pdf": {"mime_type": "application/pdf"},
                                },
                            },
                        },
                    },
                    "encoding": {"file_metadata": {"contentType": "application/json"}},
                }
            },
        }


class BatchJobSubmitAutoSchema(_BatchRequestBodyMixin, AutoSchema):
    """Several files in one multipart body (`files` repeated); one job per file, all or none."""

    def get_operation_id(self, path, method):
        return "submitJobBatch"

    def get_responses(self, path, method):
        return {
            "202": {
                "description": "Batch accepted. `Location` points at the batch status resource.",
                "headers": {"Location": {"schema": {"type": "string"}}},
                "content": _json_only(load_job_batch_schema()),
            },
            "400": _job_error("No files, too many files, an empty file, a request-level object identifier, or an invalid `file_metadata` (not JSON, an unknown or ambiguous filename, a bad value)."),
            "404": _job_error("Asynchronous processing is disabled (`MES_ASYNC_ENABLED`)."),
            "413": _job_error("The files together exceed the configured maximum size (`MES_MAX_UPLOAD_BYTES`)."),
            "500": _job_error("Unexpected error while processing the request."),
            "503": _job_error("The job store cannot take every file (`MES_ASYNC_MAX_JOBS`); no job was created."),
        }


class BatchJobStatusAutoSchema(AutoSchema):
    def get_operation_id(self, path, method):
        return "getJobBatch"

    def get_path_parameters(self, path, method):
        return [deepcopy(_BATCH_ID_PARAMETER)]

    def get_responses(self, path, method):
        return {
            "200": {
                "description": "The batch with the current status of each file's job (JSON only; `Accept: application/xml` is answered with `406`).",
                "content": _json_only(load_job_batch_schema()),
            },
            "404": _job_error("Unknown or expired batch, or asynchronous processing is disabled."),
        }
