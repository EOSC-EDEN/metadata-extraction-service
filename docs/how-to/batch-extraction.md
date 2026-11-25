# Extract several files at once

`POST /api/v1/extract/` accepts exactly one file. For several, use the asynchronous batch route `POST /api/v1/jobs/batch/`: a multipart body with the `files` field repeated once per file. There is no synchronous batch route. A batch needs asynchronous processing to be enabled (`MES_ASYNC_ENABLED`, on by default); when it is off, `/jobs/batch/` answers `404`, and the files have to be sent one by one to `/extract/`.

```bash
curl -s -F files=@a.jpg -F files=@b.pdf http://localhost:8000/api/v1/jobs/batch/
curl -s http://localhost:8000/api/v1/job-batches/<batch_id>/
curl -s http://localhost:8000/api/v1/jobs/<job_id>/result/
```

`/jobs/batch/` creates **one ordinary job per file** and answers `202` with a `Location` header and the batch envelope (`batch_id`, `created_at`, `status`, and `jobs` with `index`, `filename`, `job_id`, `status` per file). Poll the batch URL for current statuses, then fetch each result from `/api/v1/jobs/{job_id}/result/` (JSON, or `Accept: application/premis+xml`). There is no combined result route, so a failed file never hides the others. The envelopes are JSON only.

## Rules

- At most `MES_MAX_FILES_PER_REQUEST` files (default 10; more is `400`). `MES_MAX_UPLOAD_BYTES` applies to the whole request body (`413`).
- `puid` and `mime_type` are optional and apply to every file that has no value of its own (see [Describe individual files](#describe-individual-files)); without them each file is identified separately by FFIS, when it is enabled.
- A request-level `object_identifier` or `object_identifier_type` is not accepted (`400`), because one identifier cannot name several files. Give it per file instead. A file without its own is linked in its PREMIS event by type `filepath` and the uploaded filename.
- Each file's job gets its own `request_id` (`<request id>-<index>`) in the logs and the PREMIS event.
- Problems with the request itself (no files, an empty file, too many files, too large, an object identifier, malformed multipart) make the whole request invalid: `400` or `413` with `{"errors": [...]}`, and no job is created.
- Admission is all or nothing: if the job store cannot take every file (`MES_ASYNC_MAX_JOBS`), the whole batch is refused with `503` and no job is created.
- The aggregate `status` is `pending`, `running`, `succeeded`, `failed` or `partial` (all finished, some failed).
- A batch expires as a whole, `MES_JOB_TTL_SECONDS` after its last job finished. Then the batch and all its jobs answer `404`.
- Out of scope: unpacking archives.

## Describe individual files

To give one file its own `puid`, `mime_type` or object identifier, add a `file_metadata` field: a JSON object whose keys are the uploaded filenames.

```bash
curl -s http://localhost:8000/api/v1/jobs/batch/ \
  -F files=@a.jpg -F files=@b.pdf -F files=@c.tif \
  -F 'file_metadata={
        "a.jpg": {"puid": "fmt/43",
                  "object_identifier": "3f1c9a52-7d0e-4b8a-9a52-0c1e2f3a4b5c",
                  "object_identifier_type": "UUID"},
        "b.pdf": {"mime_type": "application/pdf"}
      }'
```

Here `c.tif` has no entry, so it gets the request-level values, or is identified by FFIS if there are none.

The same in Python:

```python
import json, requests

files = ["a.jpg", "b.pdf", "c.tif"]
metadata = {
    "a.jpg": {"puid": "fmt/43", "object_identifier": "3f1c...", "object_identifier_type": "UUID"},
    "b.pdf": {"mime_type": "application/pdf"},
}

handles = [open(name, "rb") for name in files]
try:
    response = requests.post(
        "http://localhost:8000/api/v1/jobs/batch/",
        files=[("files", (name, fh)) for name, fh in zip(files, handles)],
        data={"file_metadata": json.dumps(metadata)},
        timeout=300,
    )
finally:
    for fh in handles:
        fh.close()
```

- Each entry takes `puid`, `mime_type`, `object_identifier` and `object_identifier_type`, with the same limits and rules as on `/extract/`: give the identifier and its type together, and blank counts as not given. Values are strings; a `null` value inside an entry is refused (`400`). Setting the whole entry to `null`, or to `{}`, means nothing for that file.
- A file's own `puid` or `mime_type` replaces the request-level one; other files keep the request-level value.
- The entries are matched by filename, never by position, so the order of the files and of the keys does not matter. The key is the filename as MES reports it in the response's `filename`: without any directory part, so a file uploaded as `dir/a.jpg` is keyed `a.jpg`.
- The request is refused with `400`, naming the key, and nothing is extracted or queued, when a key matches no uploaded file (a typo), when it matches two or more uploaded files with the same name, when `file_metadata` is not a JSON object, when an entry is not an object or has an unknown field (the removed properties, such as `format_name`, are unknown fields), or when a value breaks a rule.
- Several files may share a filename, as long as none of them has an entry.
