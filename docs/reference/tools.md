# Tools

The tools MES can call. `GET /api/v1/tools/` lists them with their state in a running service.

| Tool | Name in `MES_TOOLS` | Kind | Enabled by default | Timeout | Raw output | Notes |
|---|---|---|---|---|---|---|
| ExifTool | `exiftool` | subprocess | yes | 30 s | xml | Default tool. |
| MediaInfo | `mediainfo` | subprocess | yes | 30 s | xml | |
| FFprobe | `ffprobe` | subprocess | no | 30 s | xml | |
| ImageMagick | `imagemagick` | subprocess | no | 30 s | text | Runs `identify`. |
| metaflac | `metaflac` | subprocess | no | 30 s | text | FLAC only. |
| JHOVE | `jhove` | subprocess | no | 60 s | xml | Runs `jhove -k -h xml`. Technical properties only: the validity `status` and `messages` in the raw XML are not an MES verdict and never become errors or warnings. Select it by format with a [local rule](../how-to/local-format-rules.md). |
| veraPDF | `verapdf` | subprocess | no | 60 s | xml | PDF only. Runs `verapdf --off --extract <features>`: validation is off, so the output is a feature report. Features come from `MES_VERAPDF_FEATURES`. |
| File Scraper | `file_scraper` | subprocess | no | 120 s | json | Runs the `scraper` CLI; slow on large files. The gunicorn worker timeout (120 s) still applies to synchronous requests. |
| Apache Tika | `tika` | HTTP service | no | 60 s | json | Needs a Tika server. |

The raw output format decides how the output appears in the PREMIS XML: well-formed `xml` is embedded as elements, anything else as a `rawOutput` element (see [Result container and statuses](result-container.md)). ExifTool runs with `-X` (RDF/XML), MediaInfo with `--Output=XML` and FFprobe with `-print_format xml`.

ExifTool and MediaInfo can be disabled with `MES_EXIFTOOL_ENABLED=false` and `MES_MEDIAINFO_ENABLED=false`. To enable an opt-in tool, see [Enable optional tools](../how-to/optional-tools.md). To add another, see [Add a tool](../adding-a-tool.md).

Which tools run for a request is decided by [local format rules](../how-to/local-format-rules.md): only the tools named by the matching rules run; with no matching rule, only the default tool runs. Selection through a File Format Library is planned but not implemented.
