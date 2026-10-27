# Use asynchronous jobs and Redis

Asynchronous processing is on by default. `POST /api/v1/extract/` stays synchronous; a job runs the same extraction on a bounded background thread pool, so its result is identical.

A job is `failed` only when extraction could not run at all (for example no default tool is configured). A run in which a mandatory tool failed still `succeeded` and reports that in the result's `errors`.

## Turn it off

Set `MES_ASYNC_ENABLED=false`. The `/jobs/` routes then answer `404`, `async_enabled` in `/api/v1/limits/` is `false`, and the UI hides its option.

## Choose a job store

`MES_JOB_STORE` selects where job state lives.

| Store | Behaviour | Constraint |
|---|---|---|
| `memory` (default) | Jobs are lost on restart and visible only to the process that created them. | Keep `WEB_CONCURRENCY=1` (use `GUNICORN_THREADS` for concurrency). With more processes a status request can answer `404 No such job`. |
| `redis` | Jobs and results survive a restart and every gunicorn worker sees them. | Set `MES_REDIS_URL`. `WEB_CONCURRENCY` can be raised. |

With Redis, a job still runs in the process that received the upload; if that worker is killed mid-job the job stays `running` until its TTL expires. Size Redis for `MES_ASYNC_MAX_JOBS` results (a result holds the raw output of every tool, so it can be large). With `appendonly` persistence, Redis keeps the jobs across its own restart too.

## Start Redis and the purge job

```bash
MES_JOB_STORE=redis WEB_CONCURRENCY=4 docker compose --profile redis up -d
```

This starts `redis` and `mes-purge`, which [removes expired jobs](limits-and-purging.md).

## Use an external Redis

The image contains only the Redis client. To use a managed or shared Redis, set `MES_JOB_STORE=redis` and `MES_REDIS_URL`, and skip the bundled service. Then note:

- The default `MES_REDIS_URL` (`redis://redis:6379/0`) resolves only while the bundled `redis` service runs, so override it.
- `mes-purge` belongs to the `redis` profile and depends on the bundled Redis. Without the profile nothing purges expired jobs, so run `python manage.py purge_expired_jobs` yourself on the schedule printed by `python manage.py purge_schedule` (for example from cron, with the same `MES_REDIS_URL` and upload directory).
