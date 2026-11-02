# Integrate FFIS

The File Format Identification Service ([FFIS](https://github.com/EOSC-EDEN/eosc-ffis)) can identify a file when the caller supplies neither a PUID nor a MIME type. The PUID it returns feeds [local format rules](local-format-rules.md).

## Enable it

| Variable | Description |
|---|---|
| `MES_FFIS_ENABLED` | `true` to call FFIS. Default `false`. |
| `MES_FFIS_BASE_URL` | Base URL of a running FFIS, for example `http://host.docker.internal:8001`. |
| `MES_FFIS_TIMEOUT_SECONDS` | Timeout of the call (default 30). |

FFIS is called only when a request has no `puid` and no `mime_type`. When it was used, the result names it in `tool_resolution.identification_url` and passes its own provenance block through in `provenance.ffis`.

!!! note
    Port 8001 is also the port the `docs` compose profile publishes. If FFIS runs on the same host on 8001, publish one of them elsewhere.
