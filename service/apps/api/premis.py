import hashlib
import shlex
import uuid

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.tool_config import get_tool_config

EVENT_TYPE = "metadata extraction"
IDENTIFICATION_EVENT_TYPE = "format identification"
#: The shared outcome rule (`classify_statuses`) -> the Library of Congress eventOutcome term.
EVENT_OUTCOME = {"success": "success", "partial": "warning", "failed": "fail"}
TOOL_AGENT_ROLE = "executing program"
SERVICE_AGENT_ROLE = "implementer"
AGENT_TYPE = "software"

#: Local identifier schemes (not Library of Congress vocabularies).
EVENT_IDENTIFIER_TYPE = "UUID"
AGENT_IDENTIFIER_TYPE = "UUID"
FALLBACK_OBJECT_IDENTIFIER_TYPE = "filepath"
OBJECT_CATEGORY = "file"
UNKNOWN_FORMAT_NAME = "unknown"
MEDIA_TYPE_REGISTRY_NAME = "IANA Media types"
#: The note that names a tool event's raw output format; the XML renderer attaches the raw output to it.
OUTPUT_FORMAT_NOTE_PREFIX = "Output format: "
EXECUTION_STATUS_NOTE_PREFIX = "Execution status: "

TOOL_NOTE_PREFIX = "Tool: "  # the first agentNote of a tool agent: its configured name, for clients that match on it
FFIS_AGENT_KEY = "ffis"
FFIS_AGENT_NAME = "File Format Identification Service (FFIS)"
FFIS_AGENT_ROLE = "executing program"
FFIS_WARNING_PREFIX = "ffis_"  # warnings made by the FFIS call itself, see tool_resolution._identify
SERVICE_AGENT_KEY = "mes-service"
SERVICE_AGENT_NAME = "Metadata Extraction Service"

#: Library of Congress controlled vocabularies: element -> (vocabulary base URI, {term: code}).
#: The XML renderer adds `authority`, `authorityURI` and `valueURI` for these terms; the JSON stays plain text.
_LOC = "http://id.loc.gov/vocabulary/preservation"
CONTROLLED_VOCABULARIES = {
    "eventOutcome": ("eventOutcome", {"success": "suc", "fail": "fai", "warning": "war"}),
    "agentType": ("agentType", {"software": "sof"}),
    "linkingAgentRole": ("eventRelatedAgentRole", {"executing program": "exe", "implementer": "imp"}),
    "messageDigestAlgorithm": ("cryptographicHashFunctions", {"SHA-256": "sha256"}),
}
FIXITY_ALGORITHM = "SHA-256"
_HASH_CHUNK_BYTES = 1024 * 1024


def vocabulary_attributes(element: str, term: str) -> dict[str, str]:
    """The authority attributes for `term` in `element`, or `{}` when it is not a controlled term."""
    entry = CONTROLLED_VOCABULARIES.get(element)
    if entry is None or term not in entry[1]:
        return {}
    vocabulary, codes = entry
    return {
        "authority": vocabulary,
        "authorityURI": f"{_LOC}/{vocabulary}",
        "valueURI": f"{_LOC}/{vocabulary}/{codes[term]}",
    }


def file_fixity(path) -> dict | None:
    """`{"size": bytes, "sha256": hex}` of the file at `path`, or None when it cannot be read.

    Called while the uploaded file still exists; without it the object simply has no size or fixity.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return None
    return {"size": size, "sha256": digest.hexdigest()}


def new_agent_ids(result) -> dict[str, str]:
    """A fresh UUID for the service, FFIS (when it was consulted) and each tool, shared by the event links and the agents."""
    names = [SERVICE_AGENT_KEY, *([FFIS_AGENT_KEY] if result.identification_url else []), *(r.tool_name for r in result.results)]
    return {name: str(uuid.uuid4()) for name in names}


def _note(text: str) -> dict:
    return {"eventOutcomeDetailNote": text}


def _summary_details(result) -> list[dict]:
    """Request-level errors (each followed by its category, as plain text) and warnings."""
    notes = []
    for error in result.error_details:
        notes.append(f"error: {error.message}")
        notes.append(f"error type: {error.category}")
    notes.extend(f"warning: {message}" for message in result.warnings)
    return [_note(note) for note in notes]


def _summary_detail(result) -> str:
    tools = ", ".join(r.tool_name for r in result.results) or "no tools"
    detail = f"Tools run: {tools}; selection_method: {result.selection_method}"
    if result.matched_rules:
        detail += f"; local rules: {', '.join(result.matched_rules)}"
    return detail


def _identification_details(result) -> list[dict]:
    """What FFIS found, as outcome notes (`PUID: ...`, `Media type: ...`, only the known parts), then its own warnings.

    The warnings are `ffis_unreachable` and `ffis_inconclusive`; the tools' warnings stay on their events.
    """
    notes = []
    if result.puid:
        notes.append(f"PUID: {result.puid}")
    if result.mime_type:
        notes.append(f"Media type: {result.mime_type}")
    notes.extend(f"warning: {m}" for m in result.warnings if m.startswith(FFIS_WARNING_PREFIX))
    return [_note(note) for note in notes]


def _interval(start, end) -> str:
    return f"{start.isoformat()}/{end.isoformat()}"


def _summary_date_time(result, timestamp: str) -> str:
    """`start/end` over all tool runs (earliest start, latest finish); the request timestamp when none ran."""
    starts = [r.started_at for r in result.results if r.started_at]
    finishes = [r.finished_at for r in result.results if r.finished_at]
    if not starts or not finishes:
        return timestamp
    return _interval(min(starts), max(finishes))


def _event(
    event_date_time: str,
    detail: str | list[str] | None,
    outcome: str,
    details: list[dict],
    links: list[dict],
    event_type: str = EVENT_TYPE,
) -> dict:
    outcome_information = {"eventOutcome": outcome}
    if details:
        outcome_information["eventOutcomeDetail"] = details
    event = {
        "eventIdentifier": {
            "eventIdentifierType": EVENT_IDENTIFIER_TYPE,
            "eventIdentifierValue": str(uuid.uuid4()),
        },
        "eventType": event_type,
        "eventDateTime": event_date_time,
    }
    if detail:
        details = [detail] if isinstance(detail, str) else detail
        event["eventDetailInformation"] = [{"eventDetail": d} for d in details]
    event["eventOutcomeInformation"] = [outcome_information]
    event["linkingAgentIdentifier"] = links
    return event


def _tool_details(tool_result) -> list[dict]:
    details = []
    if tool_result.raw_output_format:
        details.append(_note(f"{OUTPUT_FORMAT_NOTE_PREFIX}{tool_result.raw_output_format}"))
    # eventOutcome has no term for a timeout; the exact status (MES-REQ-3-008) is recorded here.
    details.append(_note(f"{EXECUTION_STATUS_NOTE_PREFIX}{tool_result.status.value}"))
    # Each error is two notes, as on the summary event: the message, then its category (`error type:`).
    for error in tool_result.error_details:
        details.append(_note(f"error: {error.message}"))
        details.append(_note(f"error type: {error.category}"))
    details.extend(_note(f"warning: {message}") for message in tool_result.warnings)
    return details


def _tool_detail(tool_result) -> str | None:
    """`Command line: ...` for a subprocess tool, `HTTP <method> <url>` for a tool called over HTTP."""
    command = tool_result.command
    if not command:
        return None
    if _tool_kind(tool_result.tool_name) == "http":
        return "HTTP " + " ".join(command)  # the adapter records (method, url)
    return f"Command line: {shlex.join(command)}"


def build_tool_event(tool_result, *, timestamp: str, agent_id: str) -> dict:
    """The `metadata extraction` event of one tool run."""
    if tool_result.started_at and tool_result.finished_at:
        date_time = _interval(tool_result.started_at, tool_result.finished_at)
    else:
        date_time = timestamp
    detail = _tool_detail(tool_result)
    outcome = "success" if tool_result.status == ToolExecutionStatus.SUCCESS else "fail"
    return _event(date_time, detail, outcome, _tool_details(tool_result), [_link(agent_id, TOOL_AGENT_ROLE)])


def _request_details(request_id, filename, timestamp) -> list[str]:
    """What identifies the request itself: its id, the name the file was uploaded with, and when it was made."""
    details = []
    if request_id:
        details.append(f"Request id: {request_id}")
    if filename:
        details.append(f"Original name: {filename}")
    if timestamp:
        details.append(f"Request timestamp: {timestamp}")
    return details


def build_identification_event(result, *, timestamp: str, agent_id: str) -> dict:
    """The `format identification` event of the FFIS call: its interval, what it found, and whether it identified the file.

    `ffis_provenance` is set (even when empty) only when FFIS identified the file; unreachable and
    inconclusive calls leave it None and are `fail`, with the FFIS warning as the outcome note.
    """
    started, finished = result.identification_started_at, result.identification_finished_at
    date_time = _interval(started, finished) if started and finished else timestamp
    outcome = "success" if result.ffis_provenance is not None else "fail"
    # No eventDetail: what FFIS found is the outcome of the event, and its URL is a note of its agent.
    return _event(
        date_time,
        None,
        outcome,
        _identification_details(result),
        [_link(agent_id, FFIS_AGENT_ROLE)],
        event_type=IDENTIFICATION_EVENT_TYPE,
    )


def build_summary_event(
    result,
    *,
    timestamp: str,
    agent_id: str,
    request_id: str | None = None,
    filename: str | None = None,
) -> dict:
    """The event MES itself writes for the request: which tools ran, how they were selected, and the combined outcome."""
    details = [_summary_detail(result), *_request_details(request_id, filename, timestamp)]
    links = [_link(agent_id, SERVICE_AGENT_ROLE)]
    return _event(
        _summary_date_time(result, timestamp),
        details,
        EVENT_OUTCOME[result.outcome],
        _summary_details(result),
        links,
    )


def build_events(
    result,
    *,
    timestamp: str,
    agent_ids: dict[str, str],
    request_id: str | None = None,
    filename: str | None = None,
) -> list[dict]:
    """The FFIS identification event (when FFIS was called), one event per tool in run order, then the summary event.

    The summary event also names the request and the file.
    """
    events = []
    if FFIS_AGENT_KEY in agent_ids:
        events.append(build_identification_event(result, timestamp=timestamp, agent_id=agent_ids[FFIS_AGENT_KEY]))
    events += [
        build_tool_event(r, timestamp=timestamp, agent_id=agent_ids[r.tool_name]) for r in result.results
    ]
    events.append(
        build_summary_event(
            result,
            timestamp=timestamp,
            agent_id=agent_ids[SERVICE_AGENT_KEY],
            request_id=request_id,
            filename=filename,
        )
    )
    return events


def _format(registry_name: str, key: str) -> dict:
    """One PREMIS `format`: its registry and key. No formatRegistryRole: PRONOM is not a specification library."""
    return {"formatRegistry": {"formatRegistryName": registry_name, "formatRegistryKey": key}}


def _formats(result) -> list[dict]:
    """A PRONOM format and an IANA media type format, whichever are known.

    The PUID is the caller's or FFIS's; the media type is FFIS's (a caller cannot give one). The names of the
    formats are not given: nothing would supply them.
    """
    formats = []
    if result.puid:
        formats.append(_format("PRONOM", result.puid))
    if result.mime_type:
        formats.append(_format(MEDIA_TYPE_REGISTRY_NAME, result.mime_type))
    if not formats:  # PREMIS requires a format; no registry key is known
        formats.append({"formatDesignation": {"formatName": UNKNOWN_FORMAT_NAME}})
    return formats


def _fixity_and_size(file_fixity: dict | None) -> dict:
    """`fixity` then `size`, as the PREMIS 3 schema orders them in `objectCharacteristics`; empty when unknown."""
    if not file_fixity:
        return {}
    return {
        "fixity": [
            {
                "messageDigestAlgorithm": FIXITY_ALGORITHM,
                "messageDigest": file_fixity["sha256"],
                "messageDigestOriginator": SERVICE_AGENT_NAME,
            }
        ],
        "size": file_fixity["size"],
    }


def build_object(
    result,
    *,
    filename: str,
    events: list[dict],
    object_identifier: str | None = None,
    object_identifier_type: str | None = None,
    file_fixity: dict | None = None,
) -> dict:
    """The file the extraction ran on, linking every event.

    `file_fixity` is the `{"size", "sha256"}` of the uploaded file (see `file_fixity()`); without it
    the object has neither `fixity` nor `size`, which are never guessed.
    """
    if object_identifier and object_identifier_type:
        object_type, object_value = object_identifier_type, object_identifier
    else:
        object_type, object_value = FALLBACK_OBJECT_IDENTIFIER_TYPE, filename
    return {
        "objectCategory": OBJECT_CATEGORY,
        "objectIdentifier": [
            {"objectIdentifierType": object_type, "objectIdentifierValue": object_value}
        ],
        "objectCharacteristics": [{**_fixity_and_size(file_fixity), "format": _formats(result)}],
        "linkingEventIdentifier": [
            {
                "linkingEventIdentifierType": e["eventIdentifier"]["eventIdentifierType"],
                "linkingEventIdentifierValue": e["eventIdentifier"]["eventIdentifierValue"],
            }
            for e in events
        ],
    }


def _link(agent_id: str, role: str) -> dict:
    return {
        "linkingAgentIdentifierType": AGENT_IDENTIFIER_TYPE,
        "linkingAgentIdentifierValue": agent_id,
        "linkingAgentRole": [role],
    }


def _agent(
    agent_id: str,
    name: str,
    version: str | None,
    tool_key: str | None = None,
    notes: list[str] | None = None,
) -> dict:
    # One identifier per agent, so the link between an event and its agent is unambiguous: extra information
    # (the tool name, the URL of FFIS) goes into agentNote.
    agent = {
        "agentIdentifier": [{"agentIdentifierType": AGENT_IDENTIFIER_TYPE, "agentIdentifierValue": agent_id}],
        "agentName": [name],
        "agentType": AGENT_TYPE,
    }
    if version is not None:
        agent["agentVersion"] = version
    notes = ([f"{TOOL_NOTE_PREFIX}{tool_key}"] if tool_key else []) + list(notes or [])
    if notes:
        agent["agentNote"] = notes
    return agent


def _tool_kind(tool_name: str) -> str | None:
    try:
        return get_tool_config(tool_name).kind
    except ToolNotConfigured:
        return None


def _display_name(tool_name: str) -> str:
    """`display_name` from `MES_TOOLS`; the tool name when none is set or the tool is no longer configured."""
    try:
        return get_tool_config(tool_name).display_name or tool_name
    except ToolNotConfigured:
        return tool_name


def _ffis_note(provenance) -> str | None:
    """A short note from FFIS's own provenance block: its outcome and the engines it ran; None when it has neither."""
    if not isinstance(provenance, dict):
        return None
    parts = []
    if provenance.get("outcome"):
        parts.append(f"outcome: {provenance['outcome']}")
    engines = [t.get("name") for t in provenance.get("tools") or [] if isinstance(t, dict) and t.get("name")]
    if engines:
        parts.append(f"engines: {', '.join(str(e) for e in engines)}")
    return f"FFIS provenance, {'; '.join(parts)}" if parts else None


def build_agents(result, *, service_version: str, agent_ids: dict[str, str]) -> list[dict]:
    """The MES service first, then FFIS when it was consulted, then one agent per tool that ran."""
    agents = [_agent(agent_ids[SERVICE_AGENT_KEY], SERVICE_AGENT_NAME, service_version)]
    if FFIS_AGENT_KEY in agent_ids:
        agents.append(
            _agent(
                agent_ids[FFIS_AGENT_KEY],
                FFIS_AGENT_NAME,
                None,
                # FFIS has one identifier; its URL and what it reported are notes
                notes=[f"URL: {result.identification_url}", *filter(None, [_ffis_note(result.ffis_provenance)])],
            )
        )
    agents.extend(
        _agent(
            agent_ids[r.tool_name],
            _display_name(r.tool_name),
            r.tool_version,
            tool_key=r.tool_name,
        )
        for r in result.results
    )
    return agents
