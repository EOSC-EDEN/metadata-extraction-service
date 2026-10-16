# Metadata Extraction Service

An extensible metadata extraction service that combines multiple extraction tools and selects them based on file format to provide interoperable technical metadata for digital preservation workflows.

## Status

This repository currently holds a **draft specification**, open for review by EDEN technical working groups. It does not yet contain an implementation.

## About

The Metadata Extraction Service (MES) is being developed as part of EOSC EDEN, implementing Core Preservation Process CPP-009 – Metadata Extraction.

No single extraction tool can capture all relevant internal metadata for the diverse range of file formats managed by a Trusted Digital Archive. MES addresses this by:

- Selecting and invoking one or more metadata extraction tools appropriate to a file's format, using file format identification (PRONOM PUID / MIME type) and File Format Libraries like [KU Leuven File Format Library](https://github.com/libis/teneo-format_library).
- Aggregating the raw, unmodified output of each tool, labelled with the tool that produced it and its execution status.
- Returning results through a standardised REST API, in both XML and JSON, for a single file or a full Submission/Archival Information Package (per the [SIP2AIP Factory specification](https://github.com/EOSC-EDEN/wp2-oais-packaging-information)).
- Supporting extensibility, so new extraction tools can be added behind adapters without changing the core wrapper logic.

Supported tool integrations are ExifTool, MediaInfo, FFprobe, ImageMagick, metaflac (FLAC only), and Apache Tika. File Scraper is planned.

See [`docs/specification.md`](docs/specification.md) for the full specification, including normative requirements and non-normative guidance.

## API documentation

The service generates its OpenAPI 3 description with Django REST framework's built-in schema support. All endpoints are relative to the service root:

| Endpoint | Description |
|---|---|
| `POST /api/v1/extract/` | Extract metadata from a single uploaded file (multipart: `file`, optional `puid`, `mime_type`). JSON by default, XML with `Accept: application/xml`. |
| `POST /api/v1/jobs/` | Optional asynchronous extraction: same multipart body as `/extract/`. Returns `202 Accepted` with a `Location` header and a job envelope (`job_id`, `status`, `created_at`, `errors`). |
| `GET /api/v1/jobs/{job_id}/` | Job envelope; `status` is `pending`, `running`, `succeeded` or `failed` (`errors` is filled when failed). `404` for an unknown or expired job. |
| `GET /api/v1/jobs/{job_id}/result/` | Exactly the `/extract/` result (JSON, or XML via `Accept`) once the job succeeded. `409` while unfinished or if the job failed; `404` for an unknown or expired job. |
| `GET /api/v1/limits/` | Deployment limits and features for the UI: `max_upload_bytes`, `async_enabled`. |
| `GET /api/v1/tools/` | List the tools configured for metadata extraction (name, kind, enabled, mandatory, default, timeout). Invocation details are not exposed. |
| `GET /api/v1/schema/` | OpenAPI 3 schema (YAML by default; send `Accept: application/vnd.oai.openapi+json` for JSON). |
| `GET /api/v1/docs/` | Browsable Swagger UI (loads a pinned `swagger-ui-dist` from a CDN). |
| `GET /api/v1/schema/result.json` | JSON Schema of the extraction result. |
| `GET /api/v1/schema/result.xsd` | XSD of the XML extraction result. |
| `GET /api/v1/schema/job.json`, `GET /api/v1/schema/job.xsd` | JSON Schema and XSD of the job envelope. |

### Asynchronous jobs

Enabled by default; set `MES_ASYNC_ENABLED=false` to disable it. The three `/jobs/` routes then return `404`, and `async_enabled` in `/api/v1/limits/` is `false`, which makes the UI hide its option.

`POST /api/v1/extract/` stays synchronous and unchanged. A job runs the same extraction on a bounded background thread pool, so its result is identical to the synchronous one. A job is `failed` only when extraction could not run at all (for example no default tool is configured); a run in which a mandatory tool failed still `succeeded` and reports that in the result's `errors`.

**Job storage.** `MES_JOB_STORE` selects where job state lives:

- `memory` (default): jobs are lost on restart and only visible to the process that created them, so run a single gunicorn worker process (`WEB_CONCURRENCY=1`, with `GUNICORN_THREADS=4` threads for concurrency). With more than one worker process, a status request that lands on another worker fails with `404 No such job`.
- `redis` (set `MES_REDIS_URL`, for example `redis://redis:6379/0`): job records and results are kept in Redis, so jobs survive a restart and every gunicorn worker sees them; `WEB_CONCURRENCY` can then be raised. A job still runs in the process that received the upload, so if that worker is killed mid-job the job stays `running` until its TTL expires. Size Redis for `MES_ASYNC_MAX_JOBS` results (a result holds the raw output of every tool, so it can be large); with `appendonly` persistence Redis keeps the jobs across its own restart too.

**Uploads and retention.** Uploads (job uploads and the temporary files of `/api/v1/extract/`) are written under `MES_JOB_UPLOAD_DIR`; in the container this is a volume (`mes-uploads`), so large uploads do not fill the container's disk and a restart or crash leaves the files where the purge job can find them. The uploaded file of a job is deleted as soon as the job finishes. A finished job's data is available for `MES_JOB_TTL_SECONDS` after it finishes; a job that has not finished (waiting, or running too long) gets the same time from its creation. A job whose age equals the TTL is already expired: it answers `404` immediately, whether or not it has been removed yet. Expired jobs are removed together with their uploaded file when the service next handles a job request (access or submit), and by the purge job below; a late result of a removed job is discarded. When `MES_ASYNC_MAX_JOBS` jobs are held, new submissions get `503`. Keep `MES_JOB_TTL_SECONDS` longer than the longest request: the purge also removes upload directories that no job refers to and that are older than the TTL.

**Purge job.** `python manage.py purge_expired_jobs` deletes expired jobs, their uploads, and orphaned upload directories (left behind when a worker died mid-job), logs how many it removed, and is safe to run repeatedly. It runs every TTL/4, between 1 minute and 1 hour, rounded down to whole minutes (for example TTL `600` → every 2 minutes, TTL `86400` → hourly). This interval is derived from `MES_JOB_TTL_SECONDS`; there is no separate schedule setting. `python manage.py purge_schedule` prints it as a crontab expression (`--minutes` for the number of minutes). With Docker Compose the `mes-purge` service runs it in a loop (`scripts/purge-loop.sh`) and reads the TTL at container start, so changing `MES_JOB_TTL_SECONDS` in `.env` and restarting is all that is needed. Start Redis and the purge job with:

```
MES_JOB_STORE=redis WEB_CONCURRENCY=4 docker compose --profile redis up -d
```

**Using an external Redis.** The image contains only the Python Redis client, never a Redis server, so you can skip the bundled `redis` service and point `MES_REDIS_URL` at any reachable Redis (managed or shared) together with `MES_JOB_STORE=redis`. Two things to keep in mind:

- The default `MES_REDIS_URL` (`redis://redis:6379/0`) only resolves while the bundled `redis` service runs, so it must be overridden.
- `mes-purge` belongs to the same `redis` profile and depends on the bundled `redis` service, so without `--profile redis` nothing purges expired jobs or orphaned uploads. Run `python manage.py purge_expired_jobs` yourself on the schedule printed by `purge_schedule` (for example from cron, against the same `MES_REDIS_URL` and upload directory), or start `mes-purge` with the profile, which also starts the bundled Redis.

**Persistence boundary.** MES returns results and stores nothing durable. The caller records the response (raw tool output, tool versions, timestamp, errors and warnings) in the Information Package and hands it on to AIP Versioning (CPP-021). See "Recording and persistence" in `docs/specification.md`.

## Metrics

`GET /metrics` exposes Prometheus metrics (scrape it from Prometheus and chart it in Grafana):

| Metric | Type | Labels |
|---|---|---|
| `mes_extract_requests_total` | Counter | `status`: `success`, `partial`, `failed`, `rejected`, `error` |
| `mes_extract_request_duration_seconds` | Histogram | |
| `mes_jobs_waiting` | Gauge | |
| `mes_jobs_waiting_bytes` | Gauge | |
| `mes_uploaded_file_bytes` | Histogram | |
| `mes_uploaded_bytes_total` | Counter | |
| `mes_tool_runs_total` | Counter | `tool`, `status` |
| `mes_tool_duration_seconds` | Histogram | `tool` |

Asynchronous jobs are counted in `mes_extract_requests_total` (and the duration histogram) once a worker has run them, never while they wait or if they expire first. `mes_jobs_waiting` and `mes_jobs_waiting_bytes` are gauges: the jobs still waiting for a worker at the moment `/metrics` is fetched (expired jobs are dropped first). They are read from the job store once per scrape rather than kept per worker process, so they are the true totals with any number of workers (with Redis they cover jobs of all workers). If the job store cannot be read, these two metrics are left out of that scrape; Prometheus uses base units, so convert for display, e.g. `mes_jobs_waiting_bytes / 1e6` for MB.

Accumulated GB uploaded: `increase(mes_uploaded_bytes_total[1d]) / 1e9`. In the container, gunicorn workers
share metrics through `PROMETHEUS_MULTIPROC_DIR`. The endpoint is unauthenticated: make it reachable only by
your Prometheus scraper, not through a public gateway.

```yaml
scrape_configs:
  - job_name: mes
    static_configs:
      - targets: ["mes:8000"]
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MES_MAX_UPLOAD_BYTES` | `536870912` (512 MiB) | Largest accepted upload. Larger files get HTTP `413` and no tool runs. |
| `MES_MAX_CONCURRENT_TOOLS` | `4` | Tools run concurrently within one request (integer ≥ 1; invalid values stop startup). Worst case in total is gunicorn workers × this value, so size it to the container's CPU/memory. |
| `MES_ASYNC_ENABLED` | `true` | Set to `false` to turn asynchronous processing off: `/api/v1/jobs/…` answers `404` and the UI hides "Process asynchronously". `/api/v1/extract/` is unaffected. |
| `MES_ASYNC_MAX_WORKERS` | `2` | Background threads running asynchronous jobs (integer ≥ 1). |
| `MES_ASYNC_MAX_JOBS` | `100` | Most jobs held at once; further submissions get HTTP `503` (integer ≥ 1). |
| `MES_JOB_TTL_SECONDS` | `86400` | How long a job's data stays available: counted from when it finishes, or from its creation if it never finishes (integer ≥ 1). |
| `MES_JOB_STORE` | `memory` | Where job state lives: `memory` or `redis` (see "Job storage"). Anything else stops startup. |
| `MES_REDIS_URL` | _(empty)_ | Redis URL, required when `MES_JOB_STORE=redis` (for example `redis://redis:6379/0`). |
| `MES_JOB_UPLOAD_DIR` | `/var/lib/mes/uploads` | Directory for uploads while they are processed; created at startup if missing, and startup fails if it is not writable. Mount a volume here. In local development and tests it defaults to a directory under the system temp dir. |
| `WEB_CONCURRENCY` | `1` | Gunicorn worker processes. Must stay `1` with `MES_JOB_STORE=memory`; can be raised with `redis`. |
| `GUNICORN_THREADS` | `4` | Threads per gunicorn worker process. |
| `MES_FFPROBE_ENABLED`, `MES_IMAGEMAGICK_ENABLED`, `MES_METAFLAC_ENABLED`, `MES_TIKA_ENABLED` | `false` | ExifTool and MediaInfo are enabled by default; the other tools are opt-in. Binary paths: `MES_FFPROBE_COMMAND`, `MES_IMAGEMAGICK_COMMAND`, `MES_METAFLAC_COMMAND` (the `flac` package; build with `INSTALL_METAFLAC=true`). metaflac only reads FLAC: until format-based tool selection exists, it fails (a warning) on other files, so enable it only for FLAC input. Tika server URL: `MES_TIKA_URL`. |

**Apache Tika.** MES does not bundle Tika; it calls a Tika server over HTTP (`PUT /rmeta`, 60 s timeout). Enable it with `MES_TIKA_ENABLED=true` and either:

- the bundled server: `docker compose --profile tika up -d` (image `apache/tika:4.1.0-full`; the default `MES_TIKA_URL=http://tika:9998` resolves only while this service runs), or
- an external server: set `MES_TIKA_URL` to it (for example `http://tika.example.org:9998`) and do not use the `tika` profile.

If the server is unreachable, returns an error, or times out, Tika is reported as a failed tool and results from the other tools are still returned.

## Adding extraction tools

See [`docs/adding-a-tool.md`](docs/adding-a-tool.md) for a step-by-step guide to configuring a new extraction tool.

## Architecture diagrams

See [`docs/diagrams.md`](docs/diagrams.md) for sequence diagrams of the synchronous extraction flow, the local CLI tool path (ExifTool), the hosted HTTP service path (Apache Tika) and the asynchronous job lifecycle.

## Contributing

Contributions are welcome via pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch policy and pull request process, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for expected standards of behaviour.

## License

This work is licensed under the [Creative Commons Attribution 4.0 International License](LICENSE).
