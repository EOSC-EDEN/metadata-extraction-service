# Set limits and purge jobs

## Limits

| Variable | Default | Effect |
|---|---|---|
| `MES_MAX_UPLOAD_BYTES` | 512 MiB | Larger uploads get `413` and no tool runs. For a batch it covers the whole request. |
| `MES_MAX_FILES_PER_REQUEST` | `10` | More files in a batch get `400`. |
| `MES_MAX_CONCURRENT_TOOLS` | `4` | Tools run concurrently within one request. The worst case in total is gunicorn workers times this value, so size it to the container's CPU and memory. |
| `MES_ASYNC_MAX_WORKERS` | `2` | Background threads running jobs. |
| `MES_ASYNC_MAX_JOBS` | `100` | Jobs held at once; more get `503`. |

`GET /api/v1/limits/` reports `max_upload_bytes`, `max_files_per_request` and `async_enabled`.

## Retention

A finished job's data is available for `MES_JOB_TTL_SECONDS` (default 86400) after it finishes. A job that never finishes gets the same time from its creation. A job whose age equals the TTL is already expired: it answers `404` at once, whether or not it has been removed yet.

The uploaded file of a job is deleted as soon as the job finishes. Uploads (job uploads and the temporary files of `/extract/`) are written under `MES_JOB_UPLOAD_DIR`; in the container this is the `mes-uploads` volume.

Keep `MES_JOB_TTL_SECONDS` longer than your longest request: the purge also removes upload directories that no job refers to and that are older than the TTL.

## Purge job

Expired jobs are removed when the service next handles a job request, and by the purge job:

```bash
python manage.py purge_expired_jobs
```

It deletes expired jobs, their uploads and orphaned upload directories (left when a worker died mid-job), logs how many it removed, and is safe to run repeatedly.

It should run every TTL/4, between 1 minute and 1 hour, rounded down to whole minutes (TTL `600` gives every 2 minutes; TTL `86400` hourly). There is no separate schedule setting. `python manage.py purge_schedule` prints the interval as a crontab expression (`--minutes` for the number of minutes).

With Docker Compose the `mes-purge` service (profile `redis`) runs it in a loop and reads the TTL at container start. Change `MES_JOB_TTL_SECONDS` in `.env` and restart that service.
