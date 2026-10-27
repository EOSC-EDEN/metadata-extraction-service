# Extract several files at once

`POST /api/v1/extract/` accepts exactly one file. Use the batch routes for several: a multipart body with the `files` field repeated once per file.

```bash
curl -F files=@a.jpg -F files=@b.pdf http://localhost:8000/api/v1/extract/batch/
```

The response is `200` with `{"results": [...]}`: one entry per file in upload order, with `index`, `filename` and either `result` (exactly what `/extract/` returns) or `errors`. A failing file never affects the others, so a batch with a failed file is still `200`.

## Rules

- At most `MES_MAX_FILES_PER_REQUEST` files (default 10; more is `400`). `MES_MAX_UPLOAD_BYTES` applies to the whole request body (`413`).
- `puid` and `mime_type` are optional and apply to every file; without them each file is identified separately.
- `object_identifier` and `object_identifier_type` are not accepted (`400`). Each file's PREMIS event identifies it by type `filename` and the uploaded filename.
- Files are extracted one after another, each with its own `request_id` (`<request id>-<index>`) in the logs and the PREMIS event.
- A file that cannot be processed gets an `errors` entry in its place. Problems with the request itself (no files, an empty file, too many files, too large, an object identifier, malformed multipart) make the whole request invalid: `400` or `413` with `{"errors": [...]}`, and nothing is extracted.
- Out of scope: unpacking archives and per-file `puid` or `mime_type`.

## Asynchronous batch

```bash
curl -s -F files=@a.jpg -F files=@b.pdf http://localhost:8000/api/v1/jobs/batch/
curl -s http://localhost:8000/api/v1/job-batches/<batch_id>/
curl -s http://localhost:8000/api/v1/jobs/<job_id>/result/
```

`/jobs/batch/` creates **one ordinary job per file** and answers `202` with a `Location` header and the batch envelope (`batch_id`, `created_at`, `status`, and `jobs` with `index`, `filename`, `job_id`, `status` per file). Poll the batch URL for current statuses, then fetch each result from `/api/v1/jobs/{job_id}/result/`. There is no combined result route, so a failed file never hides the others.

- The aggregate `status` is `pending`, `running`, `succeeded`, `failed` or `partial` (all finished, some failed).
- Admission is all or nothing: if the job store cannot take every file (`MES_ASYNC_MAX_JOBS`), the whole batch is refused with `503` and no job is created.
- A batch expires as a whole, `MES_JOB_TTL_SECONDS` after its last job finished. Then the batch and all its jobs answer `404`.
- Both routes answer `404` when `MES_ASYNC_ENABLED` is `false`.
