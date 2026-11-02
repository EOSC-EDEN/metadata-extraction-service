# REST API

All endpoints are relative to the service root. The service generates its OpenAPI 3 description with Django REST framework's schema support; browse it at `/api/v1/docs/`.

JSON is the default response format. Send `Accept: application/xml` for XML where a result is returned.

## Extraction

| Endpoint | Description |
|---|---|
| `POST /api/v1/extract/` | Extract metadata from one uploaded file (multipart: `file`, optional `puid`, `mime_type`, and the paired `object_identifier` and `object_identifier_type`). |
| `POST /api/v1/extract/batch/` | Several files in one request (the `files` field repeated; optional `puid` and `mime_type` for every file). `200` with `{"results": [...]}`. See [Extract several files at once](../how-to/batch-extraction.md). |

## Asynchronous jobs

| Endpoint | Description |
|---|---|
| `POST /api/v1/jobs/` | Same body as `/extract/`. `202 Accepted` with a `Location` header and a job envelope (`job_id`, `status`, `created_at`, `errors`). |
| `GET /api/v1/jobs/{job_id}/` | Job envelope. `404` for an unknown or expired job. |
| `GET /api/v1/jobs/{job_id}/result/` | Exactly the `/extract/` result once the job succeeded. `409` while unfinished or if the job failed; `404` for an unknown or expired job. |
| `POST /api/v1/jobs/batch/` | Same body as `/extract/batch/`; one job per file. `202` with a `Location` header and a batch envelope (`batch_id`, `created_at`, `status`, `jobs`). `503` (no job created) unless every file fits. |
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
| `GET /api/v1/schema/result.json`, `result.xsd` | JSON Schema and XSD of the extraction result. |
| `GET /api/v1/schema/job.json`, `job.xsd` | JSON Schema and XSD of the job envelope. |
| `GET /api/v1/schema/batch.json`, `batch.xsd` | JSON Schema and XSD of the `/extract/batch/` response (it references the result schemas unchanged). |
| `GET /api/v1/schema/job-batch.json`, `job-batch.xsd` | JSON Schema and XSD of the batch envelope of the asynchronous batch routes. |

## Errors

Problems with the request itself return `400`, `413` (too large) or `503` (job store full) with `{"errors": [...]}`. Per-tool failures are not HTTP errors: they appear in the result, see [Result container and statuses](result-container.md).
