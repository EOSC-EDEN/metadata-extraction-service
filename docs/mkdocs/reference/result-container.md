# Result container and statuses

The JSON Schema is served at `/api/v1/schema/result.json` and the XSD at `/api/v1/schema/result.xsd`. In XML the element names are camelCase (`errorCategories`, `errorDetails`).

## Top level

| Field | Description |
|---|---|
| `request_id` | Identifier of the request, also in the logs. |
| `filename` | The uploaded filename. |
| `tool_resolution` | How the tools were chosen. |
| `results` | One entry per tool that ran. |
| `provenance` | Timestamp, versions and the PREMIS event. |
| `errors`, `error_categories`, `error_details` | Top-level errors, see below. |
| `warnings` | Top-level warnings (strings, not categorised). |

### `tool_resolution`

`puid`, `mime_type`, `resolved_tools`, `selection_method`, `identification_url` (the FFIS consulted, or `null`) and `matched_rules`. `selection_method` is `local_rule` or `local_rule_via_ffis` when a [local rule](../how-to/local-format-rules.md) selected the tools.

### `results[]`

| Field | Description |
|---|---|
| `tool`, `version` | The tool and its version. |
| `status` | `success`, `failure` or `timeout`. |
| `started_at`, `finished_at` | Timestamps. |
| `command` | The command that was run (or `null`). |
| `raw_output`, `raw_output_format` | The tool's output exactly as produced, and its format. See [Why raw tool output is unmodified](../explanation/raw-output.md). |
| `errors`, `error_categories`, `error_details`, `warnings` | As for the top level. |

### `provenance`

`timestamp`, `service_version`, `tool_versions`, `matched_rules`, `ffis` (FFIS's own block, or `null`), `event` and `agents`.

## Errors and categories

Every result (per tool and top level) carries:

- `errors`: strings, unchanged from the tool.
- `error_categories`: the category of each error, aligned by index.
- `error_details`: `{message, category}` objects. Preferred for new callers.

Categories are `encrypted`, `truncated_or_broken`, `wrong_format` and `unknown` (the default). CPP-009 names these issues only as examples, so the set is MES's own interpretation, and the matching is a per-tool heuristic on the tool's message. MES does not judge format conformance and does not retry or remove encryption. A mandatory tool's top-level error carries its tool error's category.

## Status values

| Where | Values |
|---|---|
| Tool `status` | `success`, `failure`, `timeout` |
| Job `status` | `pending`, `running`, `succeeded`, `failed` |
| Batch `status` | `pending`, `running`, `succeeded`, `failed`, `partial` |
| PREMIS `eventOutcome` | `success`, `warning` (some tools failed), `fail` (no tool succeeded) |
| `mes_extract_requests_total` `status` | `success`, `partial`, `failed`, `rejected`, `error` |

## PREMIS extraction event

Every successful result carries in `provenance` a PREMIS 3.0 `metadata extraction` event (`provenance.event`) and the agents it links to (`provenance.agents`: the MES service and one per tool that ran, each with `agentType` `software` and its `agentVersion`). A job that fails before extraction has no result and so no event.

- The event identifier is the `request_id`; its datetime equals `provenance.timestamp`.
- `eventOutcome` follows the same rule as the `mes_extract_requests_total` metric.
- Each top-level error and warning is an `eventOutcomeDetailNote` prefixed `error: [category]` or `warning:`.
- `object_identifier` and `object_identifier_type` (at most 255 and 64 characters, only together) name the file's PREMIS object in `linkingObjectIdentifier`; without them it links the uploaded filename with type `filename`. They are echoed, not interpreted or logged.
- The element names are PREMIS's own camelCase, an exception to the snake_case elsewhere in the JSON, so the JSON mirrors the XML (validated by `premis.xsd`). In the XML, characters that cannot be carried are replaced with U+FFFD inside PREMIS elements; the unmodified text stays in `errors` and `warnings`.
- `MES` is a local identifier scheme for agents.

The event only describes what MES did. Storing it stays with the caller: see [the persistence boundary](../explanation/persistence-boundary.md).
