# REST API

All endpoints are relative to the service root. The service generates its OpenAPI 3 description with Django REST framework's schema support; browse it at `/api/v1/docs/`.

## Response formats

JSON is the default. The only XML form is the PREMIS 3 document, as `application/premis+xml`.

| Route | JSON | `application/premis+xml` | `application/xml` |
|---|---|---|---|
| `POST /extract/`, `GET /jobs/{job_id}/result/` | yes (default) | yes, or add `?format=premis` | `406` |
| Everything else: the job and batch envelopes, `tools`, `limits`, `local-rules`, errors | yes | `406` (`?format=premis` or `?format=xml` gives `404`) | `406` |

- Every error response is JSON, whatever was asked for. `406 Not Acceptable` has an `{"errors": [...]}` body too.
- The PREMIS document holds the file object, the events and the agents (see [Result container and statuses](result-container.md)). XML tool output is embedded in its event as a parsed copy, which is not byte-exact (whitespace, quoting and prefixes can change). Add `?raw=exact` to also get the tool's unmodified output in each event, as `rawOutput` with a `sha256` attribute (base64-encoded when the text has a carriage return or a character XML cannot carry).
- The unmodified output is always in `results[].raw_output` of the JSON response.

## Extraction

| Endpoint | Description |
|---|---|
| `POST /api/v1/extract/` | Extract metadata from one uploaded file (multipart: `file`, optional `puid` and `mime_type`, and the paired `object_identifier` and `object_identifier_type`). The properties `format_name`, `format_version` and `media_type_name`, which earlier versions accepted, are refused with `400`. |

## Asynchronous jobs

| Endpoint | Description |
|---|---|
| `POST /api/v1/jobs/` | Same body as `/extract/`. `202 Accepted` with a `Location` header and a job envelope (`job_id`, `status`, `created_at`, `errors`). |
| `GET /api/v1/jobs/{job_id}/` | Job envelope. `404` for an unknown or expired job. |
| `GET /api/v1/jobs/{job_id}/result/` | Exactly the `/extract/` result once the job succeeded. `409` while unfinished or if the job failed; `404` for an unknown or expired job. |
| `POST /api/v1/jobs/batch/` | Several files in one request (the `files` field repeated; optional `puid` and `mime_type` as defaults, and an optional `file_metadata` JSON object keyed by filename for the per-file `puid`, `mime_type` and object identifier); one job per file. See [Extract several files at once](../how-to/batch-extraction.md). `202` with a `Location` header and a batch envelope (`batch_id`, `created_at`, `status`, `jobs`). `503` (no job created) unless every file fits. |
| `GET /api/v1/job-batches/{batch_id}/` | Batch envelope with the status of each job and an aggregate `status`. `404` for an unknown or expired batch. |

All job routes answer `404` when `MES_ASYNC_ENABLED` is `false`. See [Use asynchronous jobs and Redis](../how-to/async-jobs.md).

## Service information

| Endpoint | Description |
|---|---|
| `GET /api/v1/tools/` | The configured tools: name, kind, enabled, mandatory, default, timeout. Invocation details are not exposed. |
| `GET /api/v1/limits/` | `max_upload_bytes`, `max_files_per_request`, `async_enabled`. |
| `GET /api/v1/local-rules/` | The [local format rules](../how-to/local-format-rules.md) (`name`, `puids`, `extensions`, `tools`, `match`), normalised, in file order; `[]` when none are configured. |
| `GET /metrics` | Prometheus metrics. See [Monitor the service](../how-to/monitoring.md). |

## Schemas and interactive docs

| Endpoint | Description |
|---|---|
| `GET /api/v1/schema/` | OpenAPI 3 schema (YAML by default; `Accept: application/vnd.oai.openapi+json` for JSON). |
| `GET /api/v1/docs/` | Swagger UI (loads a pinned `swagger-ui-dist` from a CDN). |
| `GET /api/v1/schema/result.json` | JSON Schema of the extraction result. |
| `GET /api/v1/schema/job.json` | JSON Schema of the job envelope (JSON only, so no XSD). |
| `GET /api/v1/schema/job-batch.json` | JSON Schema of the batch envelope of the asynchronous batch routes (JSON only, so no XSD). |

## Errors

Problems with the request itself return `400`, `406` (an XML form that is not offered), `413` (too large) or `503` (job store full) with `{"errors": [...]}`. Per-tool failures are not HTTP errors: they appear in the result, see [Result container and statuses](result-container.md).
