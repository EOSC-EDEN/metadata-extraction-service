# Environment variables and build args

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | none (required) | Django secret key. Compose refuses to start without it. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Allowed host names. |
| `MES_DEFAULT_TOOL` | `exiftool` | Tool that always runs first. |
| `MES_TOOL_RULES_FILE` | `config/tool_rules.yaml` | [Local format rules](../how-to/local-format-rules.md). Relative paths resolve against the `service` directory. A missing file means no rules; an invalid file stops startup. |
| `MES_FOOTER_TEXT` | empty | Small plain text shown in the web UI footer. |
| `MES_DOCS_URL` | empty | URL of this documentation. When set, the web UI footer links to it. |
| `MES_MAX_UPLOAD_BYTES` | `536870912` | Largest accepted upload (`413` above). |
| `MES_MAX_FILES_PER_REQUEST` | `10` | Most files in a batch (integer ≥ 1; invalid values stop startup). |
| `MES_MAX_CONCURRENT_TOOLS` | `4` | Tools run concurrently within one request (integer ≥ 1). |
| `MES_ASYNC_ENABLED` | `true` | Turns asynchronous processing on or off. |
| `MES_ASYNC_MAX_WORKERS` | `2` | Background threads running jobs (integer ≥ 1). |
| `MES_ASYNC_MAX_JOBS` | `100` | Jobs held at once; more get `503` (integer ≥ 1). |
| `MES_JOB_TTL_SECONDS` | `86400` | How long job data stays available (integer ≥ 1). |
| `MES_JOB_STORE` | `memory` | `memory` or `redis`. Anything else stops startup. |
| `MES_REDIS_URL` | `redis://redis:6379/0` in Compose, empty otherwise | Redis URL, required when `MES_JOB_STORE=redis`. |
| `MES_JOB_UPLOAD_DIR` | `/var/lib/mes/uploads` | Directory for uploads while processed; created at startup, which fails if it is not writable. In development and tests it defaults to a directory under the system temp dir. |
| `WEB_CONCURRENCY` | `1` | Gunicorn worker processes. Must stay `1` with `MES_JOB_STORE=memory`. |
| `GUNICORN_THREADS` | `4` | Threads per worker process. |
| `MES_FFIS_ENABLED` | `false` | Call [FFIS](../how-to/ffis.md). |
| `MES_FFIS_BASE_URL` | empty | FFIS base URL. |
| `MES_FFIS_TIMEOUT_SECONDS` | `30` | FFIS call timeout. |
| `MES_EXIFTOOL_ENABLED`, `MES_MEDIAINFO_ENABLED` | `true` | Set to `false` to disable ExifTool or MediaInfo. Do not disable the tool named by `MES_DEFAULT_TOOL`: requests would fail. |
| `MES_<TOOL>_ENABLED` | see [Tools](tools.md) | Enables an opt-in tool: `FFPROBE`, `IMAGEMAGICK`, `METAFLAC`, `FILE_SCRAPER`, `TIKA`. |
| `MES_<TOOL>_COMMAND` | tool name | Binary path for `EXIFTOOL`, `MEDIAINFO`, `FFPROBE`, `IMAGEMAGICK` (`identify`), `METAFLAC`, `FILE_SCRAPER` (`scraper`). |
| `MES_FILE_SCRAPER_SKIP_WELLFORMED` | `false` | Adds `--skip-wellformed-check` to File Scraper. |
| `MES_TIKA_URL` | `http://tika:9998` | Tika server URL. |

## Dockerfile build args

| Argument | Default | Installs |
|---|---|---|
| `INSTALL_EXIFTOOL` | `true` | ExifTool |
| `INSTALL_MEDIAINFO` | `true` | MediaInfo |
| `INSTALL_FFPROBE` | `false` | FFprobe |
| `INSTALL_IMAGEMAGICK` | `false` | ImageMagick |
| `INSTALL_METAFLAC` | `false` | metaflac (the `flac` package) |
| `INSTALL_FILE_SCRAPER` | `false` | File Scraper and its dependencies (several hundred MB) |

The remaining `ARG`s in `service/Dockerfile` (`FILE_SCRAPER_REF`, `JHOVE_JAR_URL`, `VERAPDF_VERSION` and so on) pin the versions of File Scraper's components.

See [Enable optional tools](../how-to/optional-tools.md).
