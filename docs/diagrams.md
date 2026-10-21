# Architecture Diagrams

These diagrams show how a request flows through MES and how the **tool adapters** are invoked.

{{ MES-REQ-1-005 }} requires the wrapper to invoke each configured extraction tool either as a
**local CLI subprocess** (ExifTool, MediaInfo, FFprobe, ImageMagick, metaflac) or as an
**externally hosted HTTP service** (Apache Tika). Both integration paths sit behind the same adapter
abstraction, so adding a tool never requires changes to the orchestrator (Requirement Group 4,
{{ MES-REQ-4-007 }}).

The choice between the two paths is a tool-integration concern, not a client-facing one: in both
cases the client talks to MES through the same REST API (Requirement Group 2). The difference is
entirely in how the *wrapper* talks to the underlying tool.

Diagrams:

1. [Synchronous extraction](#sequence-diagram--synchronous-extraction-post-apiv1extract) — the full request flow
2. [Local CLI tool path](#sequence-diagram--local-cli-tool-path-exiftool) — what a subprocess adapter does
3. [Hosted HTTP service path](#sequence-diagram--hosted-http-service-path-apache-tika) — what an HTTP adapter does
4. [Asynchronous job](#sequence-diagram--asynchronous-job-post-apiv1jobs) — submit, poll, fetch the result
5. [Synchronous batch](#sequence-diagram--synchronous-batch-post-apiv1extractbatch) — several files in one request
6. [Asynchronous batch](#sequence-diagram--asynchronous-batch-post-apiv1jobsbatch) — one job per file, polled as a batch

## Sequence diagram — synchronous extraction (`POST /api/v1/extract/`)

The complete request flow. Tools are resolved first: the file is identified (by the caller, or by FFIS
when the caller gave no identifier), and the identifier is sent to the File Format Library, which
returns the extraction tools for that format and the libraries consulted ({{ MES-REQ-3-009 }}). If the
Format Library cannot be reached, or there is no identifier, MES falls back to the configured default
tool ({{ MES-REQ-1-001 }}). The resolved tools then run concurrently in a thread pool bounded by
`MES_MAX_CONCURRENT_TOOLS`. The two per-tool paths are shown in detail in the next two diagrams.

Failure handling ({{ MES-REQ-3-006 }}, {{ MES-REQ-3-008 }}, {{ MES-REQ-5-001 }}): a failed tool is always
reported in its own result and never discards the other tools' results. Every error carries a
category (`encrypted`, `truncated_or_broken`, `wrong_format` or `unknown`) assigned by the adapter
(see the two per-tool diagrams); warnings are not categorised. A failed **optional** tool
also adds a top-level *warning*; a failed **mandatory** tool adds a top-level *error*. No tool is
mandatory in the default `MES_TOOLS` configuration.

```mermaid
sequenceDiagram
    actor Client
    participant API as ExtractMetadataView
    participant Orch as ExtractionOrchestrator
    participant Res as resolve_tools
    participant FFIS as FFIS (identification)
    participant FL as FormatLibraryClient
    participant Lib as File Format Library (external)
    participant Pool as ThreadPoolExecutor
    participant Adapter as Tool adapter (one per tool)

    Client->>API: POST /api/v1/extract/ (file, puid?, mime_type?)
    API->>API: check declared length, validate request
    alt too large or invalid
        API-->>Client: 413 or 400 error response
    end
    API->>API: save_upload to a temp dir (original suffix kept)
    API->>Orch: extract(path, puid, mime_type, request_id)
    Orch->>Res: resolve_tools(path, puid, mime_type)
    alt no PUID or MIME type supplied
        alt FFIS enabled
            Res->>FFIS: identify(path)
            alt FFIS identifies the file
                FFIS-->>Res: puid, mime_type, provenance
            else unreachable or inconclusive
                FFIS-->>Res: error or no result
                note over Res: warning recorded, no identifier
            end
        else FFIS disabled
            note over Res: no identifier
        end
    end
    alt an identifier is available (from the caller or from FFIS)
        Res->>FL: resolve(puid, mime_type)
        FL->>Lib: request the tools for this format
        Lib-->>FL: tools per format, library details
        alt resolved
            FL-->>Res: tools and the libraries used
            Res-->>Orch: tools, selection_method format_library (caller identified) or format_library_via_ffis, format_libraries_used
        else FormatLibraryUnavailable
            FL-->>Res: network, HTTP, timeout or malformed-payload error
            Res-->>Orch: default tool, selection_method default_tool_format_library_unreachable, warning
        end
    else no identifier at all
        note over Res,FL: the Format Library is never called
        Res-->>Orch: default tool, FFIS fallback selection_method, warning if FFIS failed
    end
    Orch->>Pool: submit one task per tool
    par for each tool
        Pool->>Adapter: build adapter from ToolConfig, run(path)
        Adapter-->>Pool: ToolInvocationResult (never raises)
    end
    Pool-->>Orch: results
    Orch->>Orch: failed mandatory tools become top-level errors (with the category of the tool's first error)
    Orch->>Orch: failed optional tools become top-level warnings
    Orch->>Orch: log every error and warning with the request_id
    Orch-->>API: AggregatedExtractionResult
    API->>API: record metrics, serialize result
    API-->>Client: 200 OK (JSON or XML per Accept header)
```

## Sequence diagram — local CLI tool path (ExifTool)

What `run()` does for a `SubprocessToolAdapter`. `run()` never raises: whatever `invoke()` throws is
mapped to a status. Each error message is labelled by the adapter's `classify_error()` (its own
`error_patterns`; `unknown` when nothing matches), and `error_details` holds `(message, category)` pairs.

```mermaid
sequenceDiagram
    participant Orch as ExtractionOrchestrator
    participant Reg as ToolRegistry
    participant Adapter as ExifToolAdapter
    participant CLI as exiftool (OS process)

    Orch->>Reg: get_adapter_class("exiftool")
    Reg-->>Orch: ExifToolAdapter
    Orch->>Adapter: ExifToolAdapter(invocation_command, extra_args, timeout_seconds)
    Orch->>Adapter: run(file_path)
    Adapter->>Adapter: invoke(): build_command(file_path)
    Adapter->>CLI: subprocess.run(["exiftool", "-json", *extra_args, file_path], timeout, check=True)
    alt exit 0
        CLI-->>Adapter: stdout (JSON)
        opt stderr is not empty
            note over Adapter: stderr is kept as a warning (MES-REQ-5-001)
        end
        Adapter-->>Orch: ToolInvocationResult(SUCCESS, raw_output, warnings)
    else non-zero exit or binary not found
        CLI-->>Adapter: CalledProcessError or OSError
        Adapter->>Adapter: classify_error(message) for each error
        Adapter-->>Orch: ToolInvocationResult(FAILURE, error_details)
    else timeout
        CLI-->>Adapter: TimeoutExpired
        Adapter->>Adapter: classify_error(message) for each error
        Adapter-->>Orch: ToolInvocationResult(TIMEOUT, error_details)
    end
```

## Sequence diagram — hosted HTTP service path (Apache Tika)

The same `run()` template, with a different `invoke()` and transport. Only the adapter differs; the
orchestrator and the API are unchanged.

```mermaid
sequenceDiagram
    participant Orch as ExtractionOrchestrator
    participant Reg as ToolRegistry
    participant Adapter as TikaAdapter
    participant Tika as Apache Tika (HTTP service)

    Orch->>Reg: get_adapter_class("tika")
    Reg-->>Orch: TikaAdapter
    Orch->>Adapter: TikaAdapter(base_url, timeout_seconds)
    Orch->>Adapter: run(file_path)
    Adapter->>Adapter: invoke(): build_request(file_path)
    Adapter->>Tika: PUT {base_url}/rmeta (Accept: application/json, file streamed in 64 KB chunks)
    alt 2xx
        Tika-->>Adapter: 200 OK (JSON body)
        Adapter-->>Orch: ToolInvocationResult(SUCCESS, raw_output)
    else non-2xx or connection error
        Tika-->>Adapter: HTTP error (raise_for_status) or RequestException
        Adapter->>Adapter: classify_error(message) for each error
        Adapter-->>Orch: ToolInvocationResult(FAILURE, error_details)
    else timeout
        Tika-->>Adapter: requests.exceptions.Timeout
        Adapter->>Adapter: classify_error(message) for each error
        Adapter-->>Orch: ToolInvocationResult(TIMEOUT, error_details)
    end
    note over Orch: if Tika is configured as mandatory, a FAILURE or TIMEOUT also becomes a top-level error (MES-REQ-3-006)
```

## Sequence diagram — asynchronous job (`POST /api/v1/jobs/`)

{{ MES-REQ-3-010 }}. The job runs the same `extract()` as the synchronous endpoint, so its result is
identical. The `/jobs/` routes answer 404 when `MES_ASYNC_ENABLED` is off. Job state lives in the job
store (in memory or Redis, chosen by `MES_JOB_STORE`).

```mermaid
sequenceDiagram
    actor Client
    participant API as Job views
    participant Jobs as JobService
    participant Store as JobStore
    participant Worker as worker thread
    participant Orch as ExtractionOrchestrator

    Client->>API: POST /api/v1/jobs/ (file, puid?, mime_type?)
    API->>API: check declared length, validate request, save_upload to a work dir
    API->>Jobs: submit(file_path, work_dir, ...)
    Jobs->>Jobs: purge_expired()
    Jobs->>Store: create(JobRecord status=pending)
    alt store at capacity (MES_ASYNC_MAX_JOBS)
        Store-->>Jobs: JobStoreFull
        API->>API: remove the work dir
        API-->>Client: 503 jobs_at_capacity
    else accepted
        Jobs->>Worker: executor.submit(run job)
        API-->>Client: 202 Accepted + Location: /api/v1/jobs/{job_id}/
    end

    Worker->>Store: status = running
    Worker->>Orch: extract(...), then serialize result
    Orch-->>Worker: result
    Worker->>Worker: remove the work dir, record metrics
    Worker->>Store: status = succeeded (result) or failed (errors)

    loop until finished or the job expires
        Client->>API: GET /api/v1/jobs/{job_id}/
        API->>Jobs: get(job_id)
        Jobs->>Store: get(job_id)
        alt unknown or expired
            API-->>Client: 404
        else
            API-->>Client: 200 job envelope (status)
        end
    end

    Client->>API: GET /api/v1/jobs/{job_id}/result/
    alt succeeded
        API-->>Client: 200 mes-result document
    else pending, running or failed
        API-->>Client: 409 job_not_finished
    end
```

A finished job's data is kept for `MES_JOB_TTL_SECONDS`; expired jobs and their uploads are purged on
access and on submit.

## Sequence diagram — synchronous batch (`POST /api/v1/extract/batch/`)

Several files in one multipart request (the `files` field repeated once per file; at most
`MES_MAX_FILES_PER_REQUEST`, advertised by `GET /limits/`). `puid` and `mime_type` apply to every file;
`object_identifier` is not accepted, because each file is identified by its filename. The files are
processed one after another, and each goes through the same `extract()` as `/extract/`, so its `result`
equals what that route returns for it. A request that is not acceptable (no files, too many, an empty
file, too large) is refused as a whole before anything runs. Once accepted, a file that fails gets an
`errors` entry in its place and the others are unaffected, so the response is `200`.

```mermaid
sequenceDiagram
    actor Client
    participant API as BatchExtractMetadataView
    participant Orch as ExtractionOrchestrator

    Client->>API: POST /api/v1/extract/batch/ (files[], puid?, mime_type?)
    API->>API: check declared length, validate request
    alt too large, no files, too many files, an empty file or invalid
        API-->>Client: 413 or 400 error response (nothing is extracted)
    end
    API->>API: create one temp work dir
    loop for each file, in order (index i)
        API->>API: save_upload into work_dir/i (one directory per file)
        API->>Orch: extract(path, puid, mime_type, request_id "{id}-i", filename)
        alt extracted
            Orch-->>API: AggregatedExtractionResult
            API->>API: entry {index, filename, result}
        else ToolNotConfigured or an unexpected error
            API->>API: log with the request_id, entry {index, filename, errors}
        end
    end
    API->>API: remove the work dir, record metrics
    API-->>Client: 200 OK {"results": [entry, ...]} (JSON or XML per Accept header)
```

## Sequence diagram — asynchronous batch (`POST /api/v1/jobs/batch/`)

The batch counterpart of the asynchronous job, available when `MES_ASYNC_ENABLED` is on (the routes
answer 404 otherwise). It queues one ordinary job per file, **all or none**: the job store must have
room for every file, or nothing is created. Each job then runs, and is polled and fetched, exactly as
in the previous async diagram (`GET /jobs/{job_id}/` and `/jobs/{job_id}/result/`). The batch adds only
a grouping record, so the client can poll all the jobs with one request.

```mermaid
sequenceDiagram
    actor Client
    participant API as Batch job views
    participant Jobs as JobService
    participant Store as JobStore
    participant Worker as worker thread

    Client->>API: POST /api/v1/jobs/batch/ (files[], puid?, mime_type?)
    API->>API: check declared length, validate request
    API->>API: one work dir per file, save_upload into each
    API->>Jobs: submit_batch(items, puid, mime_type, request_id)
    Jobs->>Jobs: purge_expired()
    Jobs->>Store: create_many(one pending JobRecord per file)
    alt not enough room for every file (MES_ASYNC_MAX_JOBS)
        Store-->>Jobs: JobStoreFull (no job created)
        API->>API: remove every work dir
        API-->>Client: 503 jobs_at_capacity
    else accepted
        Jobs->>Store: create_batch(BatchRecord: index, filename, job_id per file)
        loop for each file (index i)
            Jobs->>Worker: executor.submit(run job, request id "{id}-i")
        end
        note over Jobs: if the executor refuses a job, that job and the later ones are marked failed (uploads removed) and the batch is still reported
        API-->>Client: 202 Accepted + Location: /api/v1/job-batches/{batch_id}/
    end

    loop until every job has finished or the batch expires
        Client->>API: GET /api/v1/job-batches/{batch_id}/
        API->>Jobs: get_batch(batch_id)
        Jobs->>Store: get_batch(batch_id), get each job
        alt unknown or expired
            API-->>Client: 404
        else
            API-->>Client: 200 batch envelope (each file's job_id and current status)
        end
    end
```

Each job in a batch is kept for `MES_JOB_TTL_SECONDS` like any other job.
