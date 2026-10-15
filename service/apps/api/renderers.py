"""XML renderer for the extraction result (MES-REQ-2-005).

Renders the same dict the JSON renderer gets, so both formats stay in step.
The element layout is defined by `schema/mes-result.xsd`. Null values are
omitted. Strings that XML cannot carry (control characters) are emitted
base64-encoded with `encoding="base64"`, so raw tool output is never altered.
"""

import base64
import re
from xml.etree import ElementTree as ET

from rest_framework.renderers import BaseRenderer

NAMESPACE = "urn:eden:mes:result:1"
JOB_NAMESPACE = "urn:eden:mes:job:1"

_INVALID_XML_CHARS = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def _q(name):
    return f"{{{NAMESPACE}}}{name}"


def _set_text(element, value):
    value = str(value)
    if _INVALID_XML_CHARS.search(value):
        element.set("encoding", "base64")
        element.text = base64.b64encode(value.encode("utf-8")).decode("ascii")
    else:
        element.text = value


def _text(parent, name, value, *, encoded=False):
    """Append <name>value</name>; skip when value is None."""
    if value is None:
        return None
    child = ET.SubElement(parent, _q(name))
    if encoded:
        _set_text(child, value)
    else:
        child.text = str(value)
    return child


def _text_list(parent, name, values):
    container = ET.SubElement(parent, _q(name))
    for value in values:
        _text(container, "item", value, encoded=True)
    return container


def _json_value(parent, name, value):
    """Generic JSON tree, for free-form data such as FFIS provenance."""
    element = ET.SubElement(parent, _q(name))
    if isinstance(value, dict):
        obj = ET.SubElement(element, _q("object"))
        for key, member in value.items():
            node = _json_value(obj, "member", member)
            _set_text_attr(node, "name", key)
    elif isinstance(value, (list, tuple)):
        array = ET.SubElement(element, _q("array"))
        for item in value:
            _json_value(array, "item", item)
    elif value is None:
        ET.SubElement(element, _q("null"))
    elif isinstance(value, bool):
        ET.SubElement(element, _q("boolean")).text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        ET.SubElement(element, _q("number")).text = repr(value)
    else:
        _set_text(ET.SubElement(element, _q("string")), value)
    return element


def _set_text_attr(element, name, value):
    # Attribute values cannot be base64-flagged; replace unrepresentable characters.
    element.set(name, _INVALID_XML_CHARS.sub("�", str(value)))


def build_result_element(data: dict) -> ET.Element:
    root = ET.Element(_q("extractionResult"))
    _text(root, "requestId", data["request_id"])
    _text(root, "filename", data["filename"], encoded=True)

    resolution = data["tool_resolution"]
    res_el = ET.SubElement(root, _q("toolResolution"))
    _text(res_el, "puid", resolution["puid"])
    _text(res_el, "mimeType", resolution["mime_type"])
    _text_list(res_el, "resolvedTools", resolution["resolved_tools"])
    _text(res_el, "selectionMethod", resolution["selection_method"])
    _text(res_el, "identificationUrl", resolution["identification_url"])

    results_el = ET.SubElement(root, _q("results"))
    for result in data["results"]:
        item = ET.SubElement(results_el, _q("result"))
        _text(item, "tool", result["tool"])
        _text(item, "version", result["version"], encoded=True)
        _text(item, "status", result["status"])
        _text(item, "startedAt", result["started_at"])
        _text(item, "finishedAt", result["finished_at"])
        if result["command"] is not None:
            _text_list(item, "command", result["command"])
        _text(item, "rawOutput", result["raw_output"], encoded=True)
        _text(item, "rawOutputFormat", result["raw_output_format"])
        _text_list(item, "errors", result["errors"])
        _text_list(item, "warnings", result["warnings"])

    provenance = data["provenance"]
    prov_el = ET.SubElement(root, _q("provenance"))
    _text(prov_el, "timestamp", provenance["timestamp"])
    _text(prov_el, "serviceVersion", provenance["service_version"])
    versions_el = ET.SubElement(prov_el, _q("toolVersions"))
    for tool, version in provenance["tool_versions"].items():
        if version is not None:
            node = _text(versions_el, "toolVersion", version)
            _set_text_attr(node, "tool", tool)
    if provenance["ffis"] is not None:
        _json_value(prov_el, "ffis", provenance["ffis"])

    _text_list(root, "errors", data["errors"])
    _text_list(root, "warnings", data["warnings"])
    return root


_JOB_KEYS = {"job_id", "status", "created_at", "errors"}


def build_job_element(data: dict) -> ET.Element:
    """The job envelope; its own namespace (mes-job.xsd), not the result's."""
    root = ET.Element(f"{{{JOB_NAMESPACE}}}job")
    for name, key in (("jobId", "job_id"), ("status", "status"), ("createdAt", "created_at")):
        ET.SubElement(root, f"{{{JOB_NAMESPACE}}}{name}").text = str(data[key])
    errors = ET.SubElement(root, f"{{{JOB_NAMESPACE}}}errors")
    for message in data["errors"]:
        _set_text(ET.SubElement(errors, f"{{{JOB_NAMESPACE}}}item"), message)
    return root


class MESXMLRenderer(BaseRenderer):
    media_type = "application/xml"
    format = "xml"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, dict) and _JOB_KEYS <= data.keys():
            ET.register_namespace("", JOB_NAMESPACE)
            return ET.tostring(build_job_element(data), encoding="utf-8", xml_declaration=True)
        if not (isinstance(data, dict) and "results" in data):
            # Error payloads ({"errors": [...]}, DRF field errors) have no result schema.
            return self._render_error(data)
        ET.register_namespace("", NAMESPACE)
        return ET.tostring(build_result_element(data), encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _render_error(data):
        ET.register_namespace("", NAMESPACE)
        root = ET.Element(_q("error"))
        _json_value(root, "detail", data)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
