# Monitor the service

`GET /metrics` exposes Prometheus metrics. Scrape it from Prometheus and chart it in Grafana.

```yaml
scrape_configs:
  - job_name: mes
    static_configs:
      - targets: ["mes:8000"]
```

!!! warning
    The endpoint is unauthenticated. Make it reachable only by your Prometheus scraper, not through a public gateway.

## Metrics

| Metric | Type | Labels |
|---|---|---|
| `mes_extract_requests_total` | Counter | `status`: `success`, `partial`, `failed`, `rejected`, `error` |
| `mes_extract_request_duration_seconds` | Histogram | |
| `mes_jobs_waiting` | Gauge | |
| `mes_jobs_waiting_bytes` | Gauge | |
| `mes_batch_requests_total` | Counter | `status`: `success`, `partial`, `failed`, `rejected`, `accepted` |
| `mes_batch_files` | Histogram | |
| `mes_uploaded_file_bytes` | Histogram | |
| `mes_uploaded_bytes_total` | Counter | |
| `mes_tool_runs_total` | Counter | `tool`, `status` |
| `mes_tool_duration_seconds` | Histogram | `tool` |

## Notes

- Asynchronous jobs are counted in `mes_extract_requests_total` (and the duration histogram) once a worker has run them, never while they wait or if they expire first. Each file of a batch counts as one request.
- `mes_jobs_waiting` and `mes_jobs_waiting_bytes` are read from the job store once per scrape, so they are true totals with any number of workers (with Redis they cover all workers). If the job store cannot be read, these two are left out of that scrape.
- Prometheus uses base units. For MB, use `mes_jobs_waiting_bytes / 1e6`. Accumulated GB uploaded per day: `increase(mes_uploaded_bytes_total[1d]) / 1e9`.
- In the container, gunicorn workers share metrics through `PROMETHEUS_MULTIPROC_DIR`.
