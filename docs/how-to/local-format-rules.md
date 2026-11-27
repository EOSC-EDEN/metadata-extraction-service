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

## Where the shipped rules come from

The rules in `service/config/tool_rules.yaml` are not written from scratch. They are:

- **An extraction of the Format Policy Registry (FPR) in Archivematica.** The FPR's format-to-tool mappings were converted into local rules, one per format, matching on PUID and extension.
- **Two separate additions** that do not come from the FPR:
    - one rule for **FLAC** files (`flac-audio`, using `metaflac`);
    - a set of rules for **all PDF-related formats** (the `acrobat-pdf-*` rules and related PDF variants, using `jhove` and `verapdf`).

These rules are a temporary solution. They stand in for the missing integration with the File Format Library, which is the intended way to map file formats to extraction tools. Once that integration is available, the shipped rules are expected to be retired.

Edit or replace any of these rules to fit your deployment.

## Rules

- Each rule needs at least one of `puids` and `extensions`, and a non-empty `tools` list. An unknown key or invalid YAML stops startup. A tool that is not in `MES_TOOLS` does not stop startup, but a warning is logged.
- The PUID is the one you supplied or, if you supplied no PUID or MIME type and [FFIS](ffis.md) is enabled, the one FFIS identified.
- The extension comes from the uploaded filename (`photo.JPG` becomes `jpg`; for `a.tar.gz` only `gz`).
- All matching rules apply and their tools are combined. When at least one rule matches, **only** the matched tools run; the default tool (`MES_DEFAULT_TOOL`) does not run.
- The default tool is the fallback. It runs as the only tool if no rule matches (for example, when no format could be identified) or if the matching rules name only disabled tools.
- A rule naming a disabled tool skips it and adds the warning `tool_rule_disabled_tool`. A rule naming a tool that is not in `MES_TOOLS` skips it and adds the warning `tool_rule_unknown_tool`.

## See what applies

```bash
curl -s http://localhost:8000/api/v1/local-rules/
```

The command lists the rules the service has loaded. A result then records which rules were applied to a file:

- `tool_resolution.selection_method` states how the tools were chosen: `local_rule`, or `local_rule_via_ffis` when FFIS supplied the PUID.
- `tool_resolution.matched_rules` and `provenance.matched_rules` both list the names of the rules that matched.
- The `Tools run: …` detail of the PREMIS summary event also names the matched rules.

!!! warning
    Changes to the rules file take effect without a restart: the service checks the file on every request and reloads it when it has changed.

    If the file is invalid, every request fails until you fix it. A file that is only half saved can also be invalid, so do not edit the rules file in place. Instead, write your changes to a temporary file in the same folder, then rename it over the rules file (for example `mv tool_rules.yaml.new tool_rules.yaml`).
