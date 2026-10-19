"""Local format rules: link PUIDs and/or file extensions to extraction tools.

The rules live in a YAML file (`settings.MES_TOOL_RULES_FILE`), not in a database: they are
deployment config. `validate_tool_rules()` runs from `ExtractionConfig.ready()` so a bad file fails
at startup. A missing file means no rules. Matching is pure (`match_rules`); `tool_resolution.py`
decides what a match means for the tools that run.

File shape::

    rules:
      - name: flac-audio          # unique, required
        puids: [fmt/279]          # PRONOM identifiers, case-insensitive
        extensions: [flac]        # no leading dot needed, case-insensitive
        tools: [metaflac]         # names from settings.MES_TOOLS
        match: any                # optional: "any" (PUID or extension, default) or "all" (both)
"""

import os
from dataclasses import dataclass

import yaml
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MATCH_MODES = ("any", "all")
_RULE_KEYS = {"name", "puids", "extensions", "tools", "match"}


@dataclass(frozen=True)
class ToolRule:
    name: str
    puids: tuple[str, ...]
    extensions: tuple[str, ...]
    tools: tuple[str, ...]
    match: str = "any"


def normalise_extension(value) -> str | None:
    """Lower-case, no leading dot; None when nothing is left."""
    if not isinstance(value, str):
        return None
    return value.strip().lstrip(".").strip().lower() or None


def normalise_puid(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _rules_path() -> str:
    value = getattr(settings, "MES_TOOL_RULES_FILE", None)
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"settings.MES_TOOL_RULES_FILE must be a file path, got {value!r}.")
    return value if os.path.isabs(value) else str(settings.BASE_DIR / value)


def _string_list(rule_name, key, value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ImproperlyConfigured(f"Tool rule '{rule_name}': '{key}' must be a list of strings.")
    return value


def _build_rule(index, entry) -> ToolRule:
    if not isinstance(entry, dict):
        raise ImproperlyConfigured(f"Tool rule #{index + 1} must be a mapping.")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ImproperlyConfigured(f"Tool rule #{index + 1} needs a non-empty 'name'.")
    name = name.strip()
    unknown = set(entry) - _RULE_KEYS
    if unknown:
        raise ImproperlyConfigured(f"Tool rule '{name}' has unknown keys: {', '.join(sorted(map(str, unknown)))}.")

    puids = tuple(p for p in map(normalise_puid, _string_list(name, "puids", entry.get("puids"))))
    raw_extensions = _string_list(name, "extensions", entry.get("extensions"))
    extensions = tuple(map(normalise_extension, raw_extensions))
    if None in puids or None in extensions:
        raise ImproperlyConfigured(f"Tool rule '{name}': puids and extensions must not be blank.")
    if not puids and not extensions:
        raise ImproperlyConfigured(f"Tool rule '{name}' needs at least one of 'puids' or 'extensions'.")

    tools = tuple(t.strip() for t in _string_list(name, "tools", entry.get("tools")))
    if not tools or "" in tools:
        raise ImproperlyConfigured(f"Tool rule '{name}' needs a non-empty 'tools' list of tool names.")

    match = entry.get("match", "any")
    if match not in MATCH_MODES:
        raise ImproperlyConfigured(f"Tool rule '{name}': 'match' must be one of {MATCH_MODES}, got {match!r}.")
    if match == "all" and not (puids and extensions):
        raise ImproperlyConfigured(f"Tool rule '{name}': 'match: all' needs both 'puids' and 'extensions'.")
    return ToolRule(name, puids, extensions, tools, match)


def _parse(text: str) -> tuple[ToolRule, ...]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImproperlyConfigured(f"The tool rules file is not valid YAML: {exc}") from exc
    if document is None:
        return ()
    if not isinstance(document, dict) or set(document) - {"rules"}:
        raise ImproperlyConfigured("The tool rules file must be a mapping with a single 'rules' key.")
    entries = document.get("rules")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise ImproperlyConfigured("'rules' in the tool rules file must be a list.")
    rules = tuple(_build_rule(i, entry) for i, entry in enumerate(entries))
    names = [rule.name for rule in rules]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ImproperlyConfigured(f"Duplicate tool rule names: {', '.join(duplicates)}.")
    return rules


_cache: dict[str, tuple[tuple[int, int], tuple[ToolRule, ...]]] = {}


def load_tool_rules() -> tuple[ToolRule, ...]:
    """The rules from the configured file; () when it does not exist. Re-read when the file changes."""
    path = _rules_path()
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ImproperlyConfigured(f"The tool rules file {path!r} cannot be read: {exc}") from exc
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ImproperlyConfigured(f"The tool rules file {path!r} cannot be read: {exc}") from exc
    rules = _parse(text)
    _cache[path] = (stamp, rules)
    return rules


def validate_tool_rules() -> None:
    """Fail at startup on a bad file or a rule naming a tool that is not in `MES_TOOLS`."""
    for rule in load_tool_rules():
        for tool in rule.tools:
            if tool not in settings.MES_TOOLS:
                raise ImproperlyConfigured(
                    f"Tool rule '{rule.name}' names '{tool}', which is not in settings.MES_TOOLS."
                )


def match_rules(rules, puid, extension) -> list[ToolRule]:
    """The rules that match, in file order. `puid` and `extension` may be un-normalised or None."""
    puid, extension = normalise_puid(puid), normalise_extension(extension)
    matched = []
    for rule in rules:
        by_puid = puid is not None and puid in rule.puids
        by_extension = extension is not None and extension in rule.extensions
        if (by_puid and by_extension) if rule.match == "all" else (by_puid or by_extension):
            matched.append(rule)
    return matched
