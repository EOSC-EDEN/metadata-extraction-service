# Configure local format rules

Local format rules link file formats to extraction tools without an external format library. They live in the YAML file named by `MES_TOOL_RULES_FILE` (default `config/tool_rules.yaml`; Docker Compose mounts `service/config` read-only at `/app/config`).

```yaml
rules:
  - name: flac-audio       # unique
    puids: [fmt/279]       # PRONOM identifiers, case-insensitive
    extensions: [flac]     # case-insensitive, leading dot optional
    tools: [metaflac]      # names from MES_TOOLS
    match: any             # optional: "any" (PUID or extension, default) or "all" (both; needs both lists)
```

## Rules

- Each rule needs at least one of `puids` and `extensions`, and a non-empty `tools` list. A tool that is not in `MES_TOOLS`, an unknown key or invalid YAML stops startup.
- The PUID is the one you supplied or, when you supplied no PUID or MIME type and [FFIS](ffis.md) is enabled, the one FFIS identified.
- The extension comes from the uploaded filename (`photo.JPG` becomes `jpg`; for `a.tar.gz` only `gz`).
- All matching rules apply and their tools are combined. When at least one rule matches, **only** those tools plus the default tool (`MES_DEFAULT_TOOL`, always first) run. With no match, tool selection is unchanged (every enabled tool runs).
- A rule naming a disabled tool skips it and adds the warning `tool_rule_disabled_tool`.

## See what applies

```bash
curl -s http://localhost:8000/api/v1/local-rules/
```

A result reports its selection in `tool_resolution.selection_method` (`local_rule`, or `local_rule_via_ffis` when FFIS supplied the PUID), the rule names in `tool_resolution.matched_rules` and `provenance.matched_rules`, and in the PREMIS event detail.

!!! warning
    The file is re-read when it changes. If it becomes invalid, requests fail until it is fixed, so edit it atomically (write a temporary file, then rename it).
