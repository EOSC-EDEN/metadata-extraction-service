# Metadata Extraction Service

An extensible metadata extraction service that combines multiple extraction tools and selects them based on file format to provide interoperable technical metadata for digital preservation workflows.

## Status

This repository currently holds a **draft specification**, open for review by EDEN technical working groups. It does not yet contain an implementation.

## About

The Metadata Extraction Service (MES) is being developed as part of EOSC EDEN, implementing Core Preservation Process CPP-009 – Metadata Extraction.

No single extraction tool can capture all relevant internal metadata for the diverse range of file formats managed by a Trusted Digital Archive. MES addresses this by:

- Selecting and invoking one or more metadata extraction tools appropriate to a file's format, using file format identification (PRONOM PUID / MIME type) and File Format Libraries like [KU Leuven File Format Library](https://github.com/libis/teneo-format_library).
- Aggregating the raw, unmodified output of each tool, labelled with the tool that produced it and its execution status.
- Returning results through a standardised REST API, in both XML and JSON, for a single file or a full Submission/Archival Information Package (per the [SIP2AIP Factory specification](https://github.com/EOSC-EDEN/wp2-oais-packaging-information)).
- Supporting extensibility, so new extraction tools can be added behind adapters without changing the core wrapper logic.

Planned tool integrations include ExifTool, MediaInfo, FFprobe, File Scraper, ImageMagick, and Apache Tika.

See [`docs/specification.md`](docs/specification.md) for the full specification, including normative requirements and non-normative guidance.

## API documentation

The service generates its OpenAPI 3 description with Django REST framework's built-in schema support. All endpoints are relative to the service root:

| Endpoint | Description |
|---|---|
| `POST /api/v1/extract/` | Extract metadata from a single uploaded file (multipart: `file`, optional `puid`, `mime_type`). JSON by default, XML with `Accept: application/xml`. |
| `GET /api/v1/tools/` | List the tools configured for metadata extraction (name, kind, enabled, mandatory, default, timeout). Invocation details are not exposed. |
| `GET /api/v1/schema/` | OpenAPI 3 schema (YAML by default; send `Accept: application/vnd.oai.openapi+json` for JSON). |
| `GET /api/v1/docs/` | Browsable Swagger UI (loads a pinned `swagger-ui-dist` from a CDN). |
| `GET /api/v1/schema/result.json` | JSON Schema of the extraction result. |
| `GET /api/v1/schema/result.xsd` | XSD of the XML extraction result. |

## Adding extraction tools

See [`docs/adding-a-tool.md`](docs/adding-a-tool.md) for a step-by-step guide to configuring a new extraction tool.

## Contributing

Contributions are welcome via pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch policy and pull request process, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for expected standards of behaviour.

## License

This work is licensed under the [Creative Commons Attribution 4.0 International License](LICENSE).
