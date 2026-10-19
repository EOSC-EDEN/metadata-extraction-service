EVENT_TYPE = "metadata extraction"
#: The shared outcome rule (`classify_statuses`) -> the Library of Congress eventOutcome term.
EVENT_OUTCOME = {"success": "success", "partial": "warning", "failed": "fail"}
AGENT_ROLE = "executing program"
AGENT_TYPE = "software"

#: Local identifier schemes (not Library of Congress vocabularies).
EVENT_IDENTIFIER_TYPE = "UUID"
AGENT_IDENTIFIER_TYPE = "MES"
FALLBACK_OBJECT_IDENTIFIER_TYPE = "filename"

SERVICE_AGENT_ID = "mes-service"
SERVICE_AGENT_NAME = "Metadata Extraction Service"


def _tool_agent_ids(result) -> list[str]:
    return [r.tool_name for r in result.results]


def _outcome_notes(result) -> list[str]:
    notes = [f"error: [{d.category}] {d.message}" for d in result.error_details]
    notes.extend(f"warning: {message}" for message in result.warnings)
    return notes


def _event_detail(result) -> str:
    tools = ", ".join(_tool_agent_ids(result)) or "no tools"
    detail = f"Metadata extraction with {tools} (selection method: {result.selection_method}"
    if result.matched_rules:
        detail += f"; local rules: {', '.join(result.matched_rules)}"
    return detail + ")."


def build_event(
    result,
    *,
    request_id: str,
    timestamp: str,
    filename: str,
    object_identifier: str | None = None,
    object_identifier_type: str | None = None,
) -> dict:
    """The one `metadata extraction` event describing this request."""
    if object_identifier and object_identifier_type:
        object_type, object_value = object_identifier_type, object_identifier
    else:
        object_type, object_value = FALLBACK_OBJECT_IDENTIFIER_TYPE, filename

    outcome = {"eventOutcome": EVENT_OUTCOME[result.outcome]}
    notes = _outcome_notes(result)
    if notes:
        outcome["eventOutcomeDetail"] = [
            {"eventOutcomeDetailNote": note} for note in notes
        ]

    return {
        "eventIdentifier": {
            "eventIdentifierType": EVENT_IDENTIFIER_TYPE,
            "eventIdentifierValue": request_id,
        },
        "eventType": EVENT_TYPE,
        "eventDateTime": timestamp,
        "eventDetailInformation": [{"eventDetail": _event_detail(result)}],
        "eventOutcomeInformation": [outcome],
        "linkingAgentIdentifier": [
            {
                "linkingAgentIdentifierType": AGENT_IDENTIFIER_TYPE,
                "linkingAgentIdentifierValue": agent_id,
                "linkingAgentRole": [AGENT_ROLE],
            }
            for agent_id in [SERVICE_AGENT_ID, *_tool_agent_ids(result)]
        ],
        "linkingObjectIdentifier": [
            {
                "linkingObjectIdentifierType": object_type,
                "linkingObjectIdentifierValue": object_value,
            }
        ],
    }


def _agent(agent_id: str, name: str, version: str | None) -> dict:
    agent = {
        "agentIdentifier": [
            {
                "agentIdentifierType": AGENT_IDENTIFIER_TYPE,
                "agentIdentifierValue": agent_id,
            }
        ],
        "agentName": [name],
        "agentType": AGENT_TYPE,
    }
    if version is not None:
        agent["agentVersion"] = version
    return agent


def build_agents(result, *, service_version: str) -> list[dict]:
    """The MES service first, then one agent per tool that ran."""
    agents = [_agent(SERVICE_AGENT_ID, SERVICE_AGENT_NAME, service_version)]
    agents.extend(
        _agent(r.tool_name, r.tool_name, r.tool_version) for r in result.results
    )
    return agents
