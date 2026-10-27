# Enable optional tools

ExifTool and MediaInfo are installed and enabled by default; set `MES_EXIFTOOL_ENABLED=false` or `MES_MEDIAINFO_ENABLED=false` to disable one. Keep the tool named by `MES_DEFAULT_TOOL` (ExifTool by default) enabled, or change `MES_DEFAULT_TOOL` first. The other tools are opt-in, in two steps: install the tool in the image with a build argument, then enable it with an environment variable.

| Tool | Build argument | Enable with | Notes |
|---|---|---|---|
| FFprobe | `INSTALL_FFPROBE=true` | `MES_FFPROBE_ENABLED=true` | Command: `MES_FFPROBE_COMMAND`. |
| ImageMagick | `INSTALL_IMAGEMAGICK=true` | `MES_IMAGEMAGICK_ENABLED=true` | Command (`identify`): `MES_IMAGEMAGICK_COMMAND`. |
| metaflac | `INSTALL_METAFLAC=true` | `MES_METAFLAC_ENABLED=true` | FLAC only; it fails with a warning on other files, so enable it for FLAC input or select it with a [local rule](local-format-rules.md). |
| File Scraper | `INSTALL_FILE_SCRAPER=true` | `MES_FILE_SCRAPER_ENABLED=true` | Adds several hundred MB. `MES_FILE_SCRAPER_SKIP_WELLFORMED=true` gives a lighter, metadata-only run. It exits 0 for files that are not well-formed: check `well-formed` and `errors` in its JSON. |
| Apache Tika | none (separate server) | `MES_TIKA_ENABLED=true` | See below. |

Set both in `service/.env`, then rebuild:

```bash
INSTALL_METAFLAC=true MES_METAFLAC_ENABLED=true docker compose up --build -d
```

Check the result with `curl http://localhost:8000/api/v1/tools/`.

## Apache Tika

MES does not bundle Tika; it calls a Tika server over HTTP (`PUT /rmeta`, 60 s timeout). Enable it with `MES_TIKA_ENABLED=true` and either:

- the bundled server: `docker compose --profile tika up -d` (image `apache/tika:4.1.0-full`; the default `MES_TIKA_URL=http://tika:9998` resolves only while this service runs), or
- an external server: set `MES_TIKA_URL` (for example `http://tika.example.org:9998`) and do not use the `tika` profile.

If the server is unreachable, returns an error or times out, Tika is reported as a failed tool and the other tools' results are still returned.

## Add a tool that is not listed

See [Add a tool](../../adding-a-tool.md).
