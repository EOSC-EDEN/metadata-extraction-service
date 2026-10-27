# Metadata Extraction Service

The Metadata Extraction Service (MES) is an extensible service that combines several extraction tools and selects them by file format, to provide interoperable technical metadata for digital preservation workflows. It is developed as part of EOSC EDEN and implements Core Preservation Process CPP-009, Metadata Extraction.

MES does the following:

- Runs one or more extraction tools on a file. Today every enabled tool runs, or the tools named by [local format rules](how-to/local-format-rules.md) plus the default tool.
- Aggregates the raw, unmodified output of each tool, labelled with the tool and its execution status.
- Returns the result through a REST API in JSON or XML, for one or more files per request.
- Lets you add new tools behind adapters without changing the core logic.

## Scope

MES extracts **technical metadata** only. It does not identify formats itself (it can ask [FFIS](how-to/ffis.md)), validate conformance, or store results: see [the persistence boundary](explanation/persistence-boundary.md).

## Status

!!! warning "Draft"
    The [specification](../specification.md) is a **draft**, open for review by EDEN technical working groups. The implementation in `service/` is work in progress and does not cover everything in it. See "Not yet supported" in [Use cases and actors](../use-cases.md).

## Where to start

| You want to | Read |
|---|---|
| Try the service | [First extraction with curl](tutorials/first-extraction-curl.md) |
| Run it | [Run with Docker Compose](how-to/docker-compose.md) |
| Call the API | [REST API](reference/rest-api.md) |
| Configure it | [Environment variables and build args](reference/configuration.md) |
| Understand the design | [Use cases and actors](../use-cases.md), [Architecture diagrams](../diagrams.md) |
