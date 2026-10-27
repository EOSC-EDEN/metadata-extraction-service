# Tools

The tools MES can call. `GET /api/v1/tools/` lists them with their state in a running service.

| Tool | Name in `MES_TOOLS` | Kind | Enabled by default | Timeout | Notes |
|---|---|---|---|---|---|
| ExifTool | `exiftool` | subprocess | yes | 30 s | Default tool. |
| MediaInfo | `mediainfo` | subprocess | yes | 30 s | |
| FFprobe | `ffprobe` | subprocess | no | 30 s | |
| ImageMagick | `imagemagick` | subprocess | no | 30 s | Runs `identify`. |
| metaflac | `metaflac` | subprocess | no | 30 s | FLAC only. |
| File Scraper | `file_scraper` | subprocess | no | 120 s | Runs the `scraper` CLI; slow on large files. The gunicorn worker timeout (120 s) still applies to synchronous requests. |
| Apache Tika | `tika` | HTTP service | no | 60 s | Needs a Tika server. |

ExifTool and MediaInfo can be disabled with `MES_EXIFTOOL_ENABLED=false` and `MES_MEDIAINFO_ENABLED=false`. To enable an opt-in tool, see [Enable optional tools](../how-to/optional-tools.md). To add another, see [Add a tool](../../adding-a-tool.md).

Which tools run for a request is decided by [local format rules](../how-to/local-format-rules.md): with no matching rule every enabled tool runs, otherwise only the tools named by the matching rules plus the default tool. Selection through a File Format Library is planned but not implemented.
