# Metadata Extraction Service

An extensible metadata extraction service that combines multiple extraction tools and selects them based on file format to provide interoperable technical metadata for digital preservation workflows.

## Status

This repository holds a **draft specification**, open for review by EDEN technical working groups, and a work-in-progress implementation of it in `service/`. The implementation does not yet cover everything in the specification: see "Not yet supported" in [`docs/use-cases.md`](docs/use-cases.md) for the gaps.

## About

The Metadata Extraction Service (MES) is being developed as part of EOSC EDEN, implementing Core Preservation Process CPP-009 – Metadata Extraction.

No single extraction tool can capture all relevant internal metadata for the diverse range of file formats managed by a Trusted Digital Archive. MES addresses this by:

- Invoking one or more metadata extraction tools for a file. Today every enabled tool runs, or the tools named by local format rules (matching a PUID or file extension) plus the default tool. The caller can supply a PUID or MIME type, or the service can identify the file through FFIS. Selection through a File Format Library such as the [KU Leuven File Format Library](https://github.com/libis/teneo-format_library) is planned but not implemented.
- Aggregating the raw, unmodified output of each tool, labelled with the tool that produced it and its execution status.
- Returning results through a standardised REST API, in both XML and JSON, for one or more files per request. Extraction from a whole Submission/Archival Information Package (per the [SIP2AIP Factory specification](https://github.com/EOSC-EDEN/wp2-oais-packaging-information)) is not implemented yet.
- Supporting extensibility, so new extraction tools can be added behind adapters without changing the core wrapper logic.

Supported tool integrations are ExifTool, MediaInfo, FFprobe, ImageMagick, metaflac (FLAC only), File Scraper (CLI, opt-in), and Apache Tika.

See [`docs/specification.md`](docs/specification.md) for the full specification, including normative requirements and non-normative guidance.

## Quick start

```bash
cd service
cp .env.example .env     # set DJANGO_SECRET_KEY
docker compose up --build -d
curl -s -F file=@photo.jpg http://localhost:8000/api/v1/extract/
```

A [terminal recording](https://asciinema.org/a/HyioKG5kRr8lRdvF) shows a full extraction. The API is also described on a running service at `/api/v1/docs/` (Swagger UI) and `/api/v1/schema/` (OpenAPI 3).

## Adding extraction tools

See [`docs/adding-a-tool.md`](docs/adding-a-tool.md) for a step-by-step guide to configuring a new extraction tool.

## Architecture diagrams

See [`docs/diagrams.md`](docs/diagrams.md) for sequence diagrams of the synchronous extraction flow, the local CLI tool path (ExifTool), the hosted HTTP service path (Apache Tika) and the asynchronous job lifecycle.

## Documentation

An MkDocs documentation site is available; its Markdown sources are in [`docs/`](docs/mkdocs/). It covers:

- tutorials: [first extraction with curl](docs/mkdocs/tutorials/first-extraction-curl.md) and [in the web UI](docs/mkdocs/tutorials/first-extraction-web-ui.md)
- how-to guides: [Docker Compose](docs/mkdocs/how-to/docker-compose.md), [asynchronous jobs and Redis](docs/mkdocs/how-to/async-jobs.md), [batch extraction](docs/mkdocs/how-to/batch-extraction.md), [local format rules](docs/mkdocs/how-to/local-format-rules.md), [monitoring](docs/mkdocs/how-to/monitoring.md)
- reference: [REST API](docs/mkdocs/reference/rest-api.md), [environment variables](docs/mkdocs/reference/configuration.md), [result container](docs/mkdocs/reference/result-container.md), [tools](docs/mkdocs/reference/tools.md)

The draft specification is in [`docs/specification.md`](docs/specification.md).

## Contributing

Contributions are welcome via pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch policy and pull request process, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for expected standards of behaviour.

## License

This work is licensed under the [Creative Commons Attribution 4.0 International License](LICENSE).
