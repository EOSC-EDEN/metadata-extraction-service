"""The XML form of an extraction result (MES-REQ-2-005): a standalone PREMIS 3 document.

`PremisXMLRenderer` renders the PREMIS part of the result dict (`provenance`) as `premis:premis`
(`application/premis+xml`; there is no other XML form, `application/xml` is answered with 406). The
document follows the official PREMIS 3 schema (`xsi:schemaLocation` names it). Tool errors are plain `eventOutcomeDetailNote`s. The raw output
of a tool is an element in the event's `eventOutcomeDetailExtension`: well-formed XML as itself, anything
else as a `rawOutput` element without a namespace (the extension accepts any element, and is not
validated further). Strings that XML cannot carry (control characters) are base64-encoded with
`encoding="base64"` on that element, so raw tool output is never altered.
"""

import base64
import hashlib
import io
import re
import uuid
from xml.parsers import expat
from xml.etree import ElementTree as ET

from rest_framework.renderers import BaseRenderer

from apps.api import premis

PREMIS_NAMESPACE = "http://www.loc.gov/premis/v3"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = f"{{{XSI_NAMESPACE}}}type"

_INVALID_XML_CHARS = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def _premis(name):
    return f"{{{PREMIS_NAMESPACE}}}{name}"


def _premis_element(parent, name, value):
    """Append the PREMIS dict/list/str `value` as <premis:name>, element names taken from the dict keys.

    PREMIS text elements are plain strings that allow no attributes, so the base64 marker of
    `_set_text` would make the document invalid: characters XML cannot carry are replaced with
    U+FFFD here (the unmodified text stays in the `errors`/`warnings` lists, which are encoded).
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            _premis_element(parent, name, item)
        return
    element = ET.SubElement(parent, _premis(name))
    if isinstance(value, dict):
        for key, member in value.items():
            _premis_element(element, key, member)
    else:
        element.text = _INVALID_XML_CHARS.sub("\ufffd", str(value))
        for attribute, attribute_value in premis.vocabulary_attributes(name, str(value)).items():
            element.set(attribute, attribute_value)


#: Prefixes the embedded raw output must not take over from the document's own namespaces.
_RESERVED_PREFIXES = {"", "xml", "xmlns", "xsi", "premis", "r", "mes"}
_OWN_NAMESPACES = {PREMIS_NAMESPACE, XSI_NAMESPACE}


def _parse_xml_fragment(text):
    """The root element of well-formed XML `text`, or None. Foreign prefixes are kept for readable output.

    A document that declares entities is refused (it would be expanded into the response); it is
    then embedded as text, like any other output that is not XML.
    """
    if "<!ENTITY" in text:
        return None
    root = None
    try:
        for event, item in ET.iterparse(io.StringIO(text), events=("start-ns", "end")):
            if event == "start-ns":
                prefix, uri = item
                if prefix not in _RESERVED_PREFIXES and uri not in _OWN_NAMESPACES:
                    try:
                        ET.register_namespace(prefix, uri)
                    except ValueError:  # a prefix ElementTree reserves for generated names (ns0, ...)
                        pass
            else:
                root = item
    except (ET.ParseError, ValueError):
        return None
    return root


def _xml_root_bytes(text):
    """The root element of well-formed XML `text`, as UTF-8 bytes, or None.

    The XML declaration, the doctype and comments around the root are dropped. The check is a
    namespace-aware expat parse that builds no tree, which is far cheaper than ElementTree for large
    reports. A root without a namespace gets `xmlns=""`, so it keeps no namespace when the output
    document declares a default one. A document that declares entities is refused (see
    `_parse_xml_fragment`).
    """
    if "<!ENTITY" in text:
        return None
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    scanned = _scan_root(data, precise=False)
    if scanned is not None and scanned[1] is None:  # a comment or processing instruction follows the root
        scanned = _scan_root(data, precise=True)
    if scanned is None:
        return None
    start, end, default_ns = scanned
    root = data[start:end].rstrip()
    if not default_ns:
        name_end = re.search(rb"[\s/>]", root).start()
        if b":" not in root[:name_end]:
            root = root[:name_end] + b' xmlns=""' + root[name_end:]
    return root


def _scan_root(data, *, precise):
    """`(start, end, default_ns)` of the root element in `data` (UTF-8), or None when it is not well-formed.

    The fast scan sees only the root's start, so it cannot tell where the root ends: `end` is the end of
    the data, or None when a comment or processing instruction follows the root (the caller then scans
    `precise`, which follows every element). `default_ns` says whether the root declares a default namespace.
    """
    parser = expat.ParserCreate("utf-8", " ")
    state = {"depth": 0, "start": None, "end": None, "default_ns": False}

    def declare(prefix, uri):
        if state["depth"] == 0 and prefix is None:
            state["default_ns"] = True

    def start(name, attributes):
        if state["depth"] == 0:
            state["start"] = parser.CurrentByteIndex
            if not precise:  # nothing else is needed: stop calling Python for every element
                parser.StartElementHandler = None
                parser.StartNamespaceDeclHandler = None
        state["depth"] += 1

    def end(name):
        state["depth"] -= 1

    def after_root(*args):
        if state["depth"] == 0 and state["start"] is not None and state["end"] is None:
            state["end"] = parser.CurrentByteIndex

    parser.StartNamespaceDeclHandler = declare
    parser.StartElementHandler = start
    if precise:
        parser.EndElementHandler = end
        parser.CommentHandler = after_root
        parser.ProcessingInstructionHandler = after_root
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        return None
    if state["start"] is None:
        return None
    if not precise:
        tail = data.rstrip()
        # The end tag of the root ends in `>` after a name or `/`; a trailing comment or processing
        # instruction ends in `-->` or `?>`.
        if tail.endswith(b"-->") or tail.endswith(b"?>"):
            return state["start"], None, state["default_ns"]
        return state["start"], len(tail), state["default_ns"]
    return state["start"], state["end"] if state["end"] is not None else len(data), state["default_ns"]


def _embed_raw_output(event_element, result, *, exact_raw=False, fragments=None):
    """Attach a tool's raw output to the `Output format: ...` detail of its event, as the XML itself.

    Output that is not well-formed XML (JSON, text, a truncated report) becomes a `rawOutput`
    element with the text, base64-encoded when XML cannot carry it. `results[].rawOutput` always
    keeps the unmodified output; XML output is embedded as a parsed copy for PREMIS readers, which
    is not byte-exact. When `fragments` is given, well-formed XML is not parsed into the tree: the extension
    holds a token and `fragments` maps it to the XML bytes, which the renderer puts in place after
    serialising (see `_splice`). With `exact_raw` the unmodified text is added next to the parsed copy, with
    the SHA-256 of its UTF-8 bytes, so a reader can recover and check the original (MES-REQ-3-004).
    """
    raw = result["raw_output"]
    if raw is None:
        return
    for detail in event_element.iter(_premis("eventOutcomeDetail")):
        note = detail.find(_premis("eventOutcomeDetailNote"))
        if note is None or not (note.text or "").startswith(premis.OUTPUT_FORMAT_NOTE_PREFIX):
            continue
        extension = ET.SubElement(detail, _premis("eventOutcomeDetailExtension"))
        is_xml = False
        if result["raw_output_format"] == "xml" and fragments is not None:
            root = _xml_root_bytes(raw)
            if root is not None:
                token = "MESRAW" + uuid.uuid4().hex
                extension.text = token
                fragments[token] = root
                is_xml = True
        elif result["raw_output_format"] == "xml":
            parsed = _parse_xml_fragment(raw)
            if parsed is not None:
                extension.append(parsed)
                is_xml = True
        if not is_xml or exact_raw:
            node = ET.SubElement(extension, "rawOutput")  # no namespace, like any tool element in the extension
            node.set("format", str(result["raw_output_format"] or "text"))
            # A parser turns CR LF into LF, so text with a CR is only exact as base64.
            _set_text(node, raw, force_base64="\r" in raw)
            if exact_raw:
                node.set("sha256", hashlib.sha256(raw.encode("utf-8")).hexdigest())
        return


def _splice(document, fragments):
    """Put the XML fragments of `_embed_raw_output` in place of their tokens in the serialised `document`."""
    for token, fragment in fragments.items():
        document = document.replace(token.encode("ascii"), fragment, 1)
    return document


def _premis_events(parent, events, results, *, exact_raw=False, fragments=None):
    """The events, with each tool's raw output attached to that tool's event.

    The order is: the FFIS identification event (only when FFIS was called), one event per tool in the
    order of `results`, then the summary event. So the tool events start after the events that are
    neither a tool event nor the summary, which is one (the identification event) or none.
    """
    first_tool_event = len(events) - len(results) - 1
    for position, event in enumerate(events):
        _premis_element(parent, "event", event)
        tool_index = position - first_tool_event
        if 0 <= tool_index < len(results):
            _embed_raw_output(parent[-1], results[tool_index], exact_raw=exact_raw, fragments=fragments)


def _premis_object(parent, value):
    """<premis:object xsi:type="premis:file">; the JSON `objectCategory` becomes the xsi:type, not an element."""
    members = dict(value)
    category = members.pop("objectCategory")
    element = ET.SubElement(parent, _premis("object"))
    element.set(XSI_TYPE, f"premis:{category}")
    for key, member in members.items():
        _premis_element(element, key, member)


def _set_text(element, value, *, force_base64=False):
    value = str(value)
    if force_base64 or _INVALID_XML_CHARS.search(value):
        element.set("encoding", "base64")
        element.text = base64.b64encode(value.encode("utf-8")).decode("ascii")
    else:
        element.text = value


PREMIS_SCHEMA_LOCATION = f"{PREMIS_NAMESPACE} https://www.loc.gov/standards/premis/premis.xsd"
PREMIS_VERSION = "3.0"


def build_premis_element(results: list[dict], *, exact_raw: bool = False, fragments: dict | None = None) -> ET.Element:
    """A standalone PREMIS document for one or more extraction results.

    `exact_raw` adds the unmodified raw output to each tool event (see `_embed_raw_output`).

    All objects come first, then all events, then all agents, as the PREMIS 3 schema orders them. Every
    identifier is a fresh UUID per result, so several results never clash.
    """
    root = ET.Element(
        _premis("premis"),
        {"version": PREMIS_VERSION, f"{{{XSI_NAMESPACE}}}schemaLocation": PREMIS_SCHEMA_LOCATION},
    )
    for result in results:
        _premis_object(root, result["provenance"]["object"])
    for result in results:
        _premis_events(root, result["provenance"]["events"], result["results"], exact_raw=exact_raw, fragments=fragments)
    for result in results:
        for agent in result["provenance"]["agents"]:
            _premis_element(root, "agent", agent)
    return root


class PremisXMLRenderer(BaseRenderer):
    """The PREMIS part of a result as a standalone `premis:premis` document (`Accept: application/premis+xml`
    or `?format=premis`; add `?raw=exact` for the unmodified raw output in each tool event). It is the
    only XML form of the API."""

    media_type = "application/premis+xml"
    format = "premis"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        results = self._results(data)
        if results is None:
            # Only results are rendered; errors are answered as JSON by the exception handler and no other
            # route offers this renderer.
            raise ValueError("PremisXMLRenderer renders extraction results only.")
        ET.register_namespace("premis", PREMIS_NAMESPACE)
        ET.register_namespace("xsi", XSI_NAMESPACE)
        exact_raw = self._exact_raw(renderer_context)
        fragments = {}
        root = build_premis_element(results, exact_raw=exact_raw, fragments=fragments)
        return _splice(ET.tostring(root, encoding="utf-8", xml_declaration=True), fragments)

    @staticmethod
    def _exact_raw(renderer_context):
        """True only for `?raw=exact`: the unmodified raw output is added to each tool event."""
        request = (renderer_context or {}).get("request")
        params = getattr(request, "query_params", None) or {}
        return params.get("raw") == "exact"

    @staticmethod
    def _results(data):
        if isinstance(data, dict) and "provenance" in data and "results" in data:
            return [data]
        return None

