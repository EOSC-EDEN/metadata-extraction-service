# Why raw tool output is returned unmodified

MES puts each tool's output in `raw_output` of the JSON result exactly as produced and never re-parses it (requirement MES-REQ-3-004). It labels the output with the tool, its version and its execution status, and nothing more.

The PREMIS XML is the one place where the output is read: well-formed XML is embedded in the tool's event as elements, so a PREMIS reader can use it. That copy is parsed and re-serialised, so it is not byte-exact. Add `?raw=exact` to the request to get the unmodified text next to it, with its SHA-256; the JSON `raw_output` is always exact.

## Reasons

- **Fidelity.** Every extractor models files differently. Mapping them onto one schema loses information and invents agreement between tools that disagree. Disagreement is itself useful to a preservation specialist.
- **Provenance.** What the tool said is a fact; an interpretation is a claim. Keeping the first separate lets the archive re-interpret later without re-running the tool on a file that may no longer be available.
- **Extensibility.** A new tool needs an adapter that runs it, not a mapping into a shared model, so [adding a tool](../adding-a-tool.md) stays small.
- **Honest scope.** MES is not a validator and does not judge conformance. The only thing it adds is an `error_categories` label per error, to spare callers from parsing free text; the original messages stay unchanged in `errors`.

## Consequence

Consumers must handle each tool's format themselves (`raw_output_format` tells them which). Normalising the output is a job for a downstream component.
