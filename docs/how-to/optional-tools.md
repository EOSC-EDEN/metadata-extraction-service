# Enable optional tools

ExifTool and MediaInfo are installed and enabled by default; set `MES_EXIFTOOL_ENABLED=false` or `MES_MEDIAINFO_ENABLED=false` to disable one. Keep the tool named by `MES_DEFAULT_TOOL` (ExifTool by default) enabled, or change `MES_DEFAULT_TOOL` first. The other tools are opt-in: enabling one with its environment variable also installs it in the image, so there is no separate install setting.

| Tool | Enable with | Notes |
|---|---|---|
| FFprobe | `MES_FFPROBE_ENABLED=true` | Command: `MES_FFPROBE_COMMAND`. |
| ImageMagick | `MES_IMAGEMAGICK_ENABLED=true` | Command (`identify`): `MES_IMAGEMAGICK_COMMAND`. |
| metaflac | `MES_METAFLAC_ENABLED=true` | FLAC only; it fails with a warning on other files, so enable it for FLAC input or select it with a [local rule](local-format-rules.md). |
| JHOVE | `MES_JHOVE_ENABLED=true` | Adds a JRE. JHOVE cannot switch validation off, so its XML report always has a validity status; MES keeps it in the raw output and does not treat it as an error. Enable it for the formats its modules cover, or select it with a [local rule](local-format-rules.md). |
| veraPDF | `MES_VERAPDF_ENABLED=true` | Adds a JRE. PDF only; it fails with a warning on other files. Runs with validation off. Set `MES_VERAPDF_FEATURES` to change the extracted features. |
| File Scraper | `MES_FILE_SCRAPER_ENABLED=true` | Adds several hundred MB. `MES_FILE_SCRAPER_SKIP_WELLFORMED=true` gives a lighter, metadata-only run. It exits 0 for files that are not well-formed: check `well-formed` and `errors` in its JSON. |
| Apache Tika | `MES_TIKA_ENABLED=true` | Separate server, see below. |

Set the flag in `service/.env`, then rebuild so the tool is installed:

```bash
MES_METAFLAC_ENABLED=true docker compose up --build -d
```

Check the result with `curl http://localhost:8000/api/v1/tools/`.

## Apache Tika

MES does not bundle Tika; it calls a Tika server over HTTP (`PUT /rmeta`, 60 s timeout). Enable it with `MES_TIKA_ENABLED=true` and either:

- the bundled server: `docker compose --profile tika up -d` (image `apache/tika:4.1.0-full`; the default `MES_TIKA_URL=http://tika:9998` resolves only while this service runs), or
- an external server: set `MES_TIKA_URL` (for example `http://tika.example.org:9998`) and do not use the `tika` profile.

If the server is unreachable, returns an error or times out, Tika is reported as a failed tool and the other tools' results are still returned.

## Add a tool that is not listed

See [Add a tool](../adding-a-tool.md).
