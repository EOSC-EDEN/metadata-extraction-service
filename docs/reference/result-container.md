# Result container and statuses

The JSON Schema is served at `/api/v1/schema/result.json`. The XML form is only the PREMIS document (see below); it follows the Library of Congress `premis.xsd`. The only element MES adds is `rawOutput` inside an event's `eventOutcomeDetailExtension`; it has no namespace, as the PREMIS extension accepts any element, and is not validated beyond that.

## Top level

| Field | Description |
|---|---|
| `request_id` | Identifier of the request, also in the logs. |
| `filename` | The uploaded filename. |
| `tool_resolution` | How the tools were chosen. |
| `results` | One entry per tool that ran. |
| `provenance` | Timestamp, versions, FFIS's own block and the PREMIS `object`, `events` and `agents`. |
| `errors`, `error_categories`, `error_details` | Top-level errors, see below. |
| `warnings` | Top-level warnings (strings, not categorised). |

### `tool_resolution`

`puid`, `mime_type`, `resolved_tools`, `selection_method`, `identification_url` (the FFIS consulted, or `null`) and `matched_rules`. `selection_method` is `local_rule` or `local_rule_via_ffis` when a [local rule](../how-to/local-format-rules.md) selected the tools; otherwise the default tool ran and it is `default_tool_caller_identified` (the caller gave a `puid` or `mime_type`, FFIS was not called), `default_tool_ffis_identified` (FFIS found a format, no rule matched), `default_tool_ffis_unreachable`, `default_tool_ffis_inconclusive` or `default_tool_ffis_disabled`.

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

`timestamp`, `service_version`, `tool_versions`, `matched_rules`, `ffis` (FFIS's own block, or `null`), `object`, `events` and `agents`.

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

## PREMIS object, events and agents

Every successful result carries in `provenance` a PREMIS 3.0 file `object` (`provenance.object`, `xsi:type="premis:file"` in the XML), its events (`provenance.events`: `metadata extraction`, and `format identification` when FFIS was called) and the agents they link to (`provenance.agents`: the MES service and one per tool that ran, each with `agentType` `software`; the service has its `agentVersion`, but no built-in adapter reports its tool's version yet, so tool agents have none). A job that fails before extraction has no result and so none of these.

- **Events:** the FFIS identification event (only when FFIS was called), one event per tool that ran in run order, then one summary event written by MES. Each has a fresh UUID as `eventIdentifier`; `request_id` is not an event identifier.
- **Identification event:** present only when MES called FFIS (not when the caller gave a `puid` or `mime_type`, and not when FFIS is disabled). `eventType` is `format identification`; `eventDateTime` is the interval of the FFIS call; it has no `eventDetail`. What FFIS found is in the outcome notes (`eventOutcomeDetailNote`): `PUID: fmt/18` and `Media type: application/pdf`, only the parts that are known; the URL of FFIS is a note of its agent, not repeated here. `eventOutcome` is `success` when FFIS identified the file and `fail` when it was unreachable or could not identify it, with that FFIS warning (`ffis_unreachable: …` or `ffis_inconclusive: …`) as a further outcome note. It links the FFIS agent as `executing program`. It is also written when local rules then select the tools. A consumer such as Archivematica that records its own identification chooses which events to keep.
- **Tool event:** `eventDateTime` is the interval `started_at/finished_at` of the run; `eventDetail` is `Command line: <command>` (omitted when the tool has no command line); `eventOutcome` is `success` or `fail`; the notes give `Output format: <format>`, `Execution status: success|failure|timeout` (the exact status; `eventOutcome` has no term for a timeout), and the tool's `error:` and `warning:` messages. It links its tool agent as `executing program`.
- **Summary event:** `eventDateTime` spans the earliest start to the latest finish of all tools (the request timestamp when none ran); its `eventDetail` entries are `Tools run: a, b; selection_method: x`, plus `; local rules: …` when local rules matched, and one entry each for `Request id: …`, `Original name: <uploaded filename>` and `Request timestamp: …`; `eventOutcome` follows the same rule as the `mes_extract_requests_total` metric; each top-level error is a note `error: <message>` followed by `error type: <category>`, and each warning a note `warning: <message>`. It links only the MES service agent, as `implementer`. The format identification is in its own event, not here.
- **Object:** `objectIdentifier` is the caller's `object_identifier` and `object_identifier_type` (at most 255 and 64 characters, only together; echoed, not interpreted or logged); without them it is the uploaded filename with type `filepath`. `objectCharacteristics` lists a `format` for the PRONOM PUID (registry `PRONOM`) and one for the IANA media type (registry `IANA Media types`), whichever are known; each is the caller's (`puid`, `mime_type`) or FFIS's. There are no format names, because nothing supplies them (`format_name`, `format_version` and `media_type_name` are not request properties). With neither a PUID nor a media type, the format is a `formatDesignation` with the name `unknown`, because PREMIS requires a format. `objectCharacteristics` also has `fixity` (SHA-256 of the uploaded file as MES received it, `messageDigestOriginator` `Metadata Extraction Service`) and `size` in bytes, in that order before `format`; both are left out when MES could not read the file, never guessed. The extracted tool output is not copied into `objectCharacteristicsExtension`: it stays in the tool events. `linkingEventIdentifier` lists every event; events carry no link back to the object.
- **Raw output (XML only):** in the XML, the `Output format: <format>` note of a tool event is followed by an `eventOutcomeDetailExtension` that holds the tool's raw output. Well-formed XML (ExifTool `-X`, FFprobe, MediaInfo, JHOVE, veraPDF) is embedded as elements; anything else (a truncated report, text) becomes `<rawOutput format="...">` with the text, base64-encoded when XML cannot carry it. A document that declares entities is embedded as text. It is a second, parsed copy: `results[].raw_output` keeps the unmodified output and the JSON does not repeat it. A failed tool has none.
- **Errors:** each error of a tool event is two `eventOutcomeDetailNote`s, `error: <message>` and then `error type: <category>` (`encrypted`, `truncated_or_broken`, `wrong_format` or `unknown`), the same pair the summary event uses for request-level errors. There is no error element and no tool error id.
- **Vocabularies:** in the XML, `eventOutcome`, `agentType` and `linkingAgentRole` carry `authority`, `authorityURI` and `valueURI` for the Library of Congress terms. The JSON holds the plain terms.
- **Agents:** every agent has exactly one identifier, of type `UUID`, so the link between an event and its agent is unambiguous; extra information is in `agentNote`. The name of `exiftool`, `mediainfo` and `ffprobe` is `Exiftool`, `MediaInfo` and `ffprobe`; other tools keep their configured name. The first `agentNote` of a tool agent is `Tool: <name>` with the configured tool name, for clients that match on it (the service agent has no note). When FFIS was consulted, it is an agent too (`File Format Identification Service (FFIS)`, type `software`, with one identifier, its `UUID`; its base URL is the first `agentNote`, `URL: <url>`, followed by the note `FFIS provenance, outcome: …; engines: …` when FFIS reported them; no version, because FFIS's version is not received), listed after the service and before the tools; the identification event links it.
- **Standalone document:** `Accept: application/premis+xml` or `?format=premis` returns only the object, events and agents as a `premis:premis` (version 3.0) document, valid against `premis.xsd`. XML tool output is embedded as a parsed copy (whitespace and prefixes may change). With `?raw=exact` the unmodified text is added next to it as `rawOutput` with the SHA-256 of its UTF-8 bytes (`sha256`), base64-encoded when the text has a carriage return or a character XML cannot carry; decode, then hash, to verify. `results[].raw_output` of the normal response is always exact.
- **Breaking change:** `provenance.event` and `linkingObjectIdentifier` are replaced by `provenance.object` and `provenance.events`; ExifTool, FFprobe and MediaInfo now return XML instead of JSON as raw output.
- The element names are PREMIS's own camelCase, an exception to the snake_case elsewhere in the JSON, so the JSON mirrors the XML (validated by `premis.xsd`). In the XML, characters that cannot be carried are replaced with U+FFFD inside PREMIS elements; the unmodified text stays in `errors` and `warnings`.

The event only describes what MES did. Storing it stays with the caller: see [the persistence boundary](../explanation/persistence-boundary.md).
