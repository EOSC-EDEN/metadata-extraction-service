"""The raw output of a tool, embedded in the XML response inside its PREMIS event (not in the JSON)."""

import subprocess
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import PREMIS_NAMESPACE
from tests.api.premis_schema import premis_xsd

NS = {"p": PREMIS_NAMESPACE, "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
RDF = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
    "<rdf:Description rdf:about='upload.jpg' xmlns:File='http://ns.exiftool.org/File/1.0/'>"
    "<File:MIMEType>image/jpeg</File:MIMEType></rdf:Description></rdf:RDF>"
)


def _ok(stdout):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@mock.patch("apps.extraction.adapters.base.subprocess.run")
class RawOutputEmbeddingTests(TestCase):
    def _post_xml(self, run, stdout=None, **kwargs):
        if stdout is not None:
            run.return_value = _ok(stdout)
        else:
            run.side_effect = kwargs["side_effect"]
        response = APIClient().post(
            "/api/v1/extract/",
            {"file": SimpleUploadedFile("upload.jpg", b"data", content_type="image/jpeg"), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT="application/premis+xml",
        )
        self.assertEqual(response.status_code, 200)
        return etree.fromstring(response.content)

    def _assert_valid(self, xml):
        xsd = premis_xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)

    def _tool_extension(self, xml):
        events = xml.findall("p:event", NS)
        return events[0].find("p:eventOutcomeInformation/p:eventOutcomeDetail/p:eventOutcomeDetailExtension", NS)

    def test_xml_output_is_embedded_as_elements_after_the_output_format_note(self, run):
        xml = self._post_xml(run, RDF)

        self._assert_valid(xml)
        detail = xml.find("p:event/p:eventOutcomeInformation/p:eventOutcomeDetail", NS)
        self.assertEqual([etree.QName(c).localname for c in detail], ["eventOutcomeDetailNote", "eventOutcomeDetailExtension"])
        self.assertEqual(detail[0].text, "Output format: xml")
        mime = detail.find(".//{http://ns.exiftool.org/File/1.0/}MIMEType")
        self.assertEqual(mime.text, "image/jpeg")
        self.assertIsNotNone(detail.find("p:eventOutcomeDetailExtension/rdf:RDF", NS))

    def test_the_unmodified_output_stays_in_the_json_result(self, run):
        run.return_value = _ok(RDF)
        body = APIClient().post(
            "/api/v1/extract/",
            {"file": SimpleUploadedFile("upload.jpg", b"data", content_type="image/jpeg"), "mime_type": "image/jpeg"},
            format="multipart",
        ).json()
        self.assertEqual(body["results"][0]["raw_output"], RDF)

    def test_only_the_tool_event_carries_it_not_the_summary(self, run):
        xml = self._post_xml(run, RDF)
        events = xml.findall("p:event", NS)
        self.assertGreaterEqual(len(events), 2)  # at least one tool event and the summary event
        for tool_event in events[:-1]:
            self.assertIsNotNone(tool_event.find(".//p:eventOutcomeDetailExtension", NS))
        self.assertIsNone(events[-1].find(".//p:eventOutcomeDetailExtension", NS))

    def test_malformed_xml_is_embedded_as_text(self, run):
        xml = self._post_xml(run, "<a><b></a>")
        self._assert_valid(xml)
        node = self._tool_extension(xml).find("rawOutput")
        self.assertEqual((node.get("format"), node.text), ("xml", "<a><b></a>"))

    def test_a_document_with_entities_is_embedded_as_text(self, run):
        raw = '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'
        xml = self._post_xml(run, raw)
        self._assert_valid(xml)
        self.assertEqual(self._tool_extension(xml).find("rawOutput").text, raw)

    def test_characters_xml_cannot_carry_are_base64_encoded(self, run):
        xml = self._post_xml(run, "not xml\x00")
        self._assert_valid(xml)
        node = self._tool_extension(xml).find("rawOutput")
        self.assertEqual(node.get("encoding"), "base64")

    def test_empty_output_is_embedded_as_empty_text(self, run):
        xml = self._post_xml(run, "")
        self._assert_valid(xml)
        node = self._tool_extension(xml).find("rawOutput")
        self.assertEqual(node.text, None)

    def test_a_failed_tool_has_no_raw_output_but_its_error(self, run):
        xml = self._post_xml(run, side_effect=OSError("boom"))
        self._assert_valid(xml)
        event = xml.findall("p:event", NS)[0]
        self.assertIsNone(event.find(".//rawOutput"))
        notes = [n.text for n in event.findall(".//p:eventOutcomeDetailNote", NS)]
        self.assertTrue(any(n.startswith("error: ") for n in notes))
        self.assertIn("error type: unknown", notes)
        self.assertIsNone(event.find(".//p:eventOutcomeDetailExtension", NS))

    def test_the_json_response_is_not_doubled(self, run):
        run.return_value = _ok(RDF)
        body = (
            APIClient()
            .post(
                "/api/v1/extract/",
                {"file": SimpleUploadedFile("upload.jpg", b"data"), "mime_type": "image/jpeg"},
                format="multipart",
            )
            .json()
        )
        self.assertEqual(body["results"][0]["raw_output"], RDF)
        details = body["provenance"]["events"][0]["eventOutcomeInformation"][0]["eventOutcomeDetail"]
        self.assertEqual(
            details,
            [{"eventOutcomeDetailNote": "Output format: xml"}, {"eventOutcomeDetailNote": "Execution status: success"}],
        )


class XmlRootExtractionTests(TestCase):
    """`_xml_root_bytes` keeps the root element as text and drops what surrounds it."""

    def _root(self, text):
        from apps.api.renderers import _xml_root_bytes

        root = _xml_root_bytes(text)
        return None if root is None else root.decode()

    def test_the_declaration_doctype_comments_and_whitespace_around_the_root_are_dropped(self):
        text = '<?xml version="1.0"?>\n<!-- a --><!DOCTYPE a>\n<a><b>1</b></a>\n<!-- z --><?pi x?>\n'
        self.assertEqual(self._root(text), '<a xmlns=""><b>1</b></a>')

    def test_a_trailing_comment_or_instruction_is_dropped_exactly(self):
        for tail in ("<!-- >> -->", "<?pi a > b?>", "  <!-- 1 --> <!-- 2 -->  "):
            with self.subTest(tail=tail):
                self.assertEqual(self._root(f"<a>x</a>{tail}"), '<a xmlns="">x</a>')

    def test_comments_and_instructions_inside_the_root_stay(self):
        self.assertEqual(self._root("<a><!-- in --><?pi?></a>"), '<a xmlns=""><!-- in --><?pi?></a>')

    def test_a_namespaced_root_keeps_its_own_declarations_and_gets_no_extra(self):
        for text in ('<r:a xmlns:r="urn:x"/>', '<a xmlns="urn:x"><b/></a>'):
            with self.subTest(text=text):
                self.assertEqual(self._root(text), text)

    def test_a_self_closing_root_and_a_gt_in_an_attribute(self):
        self.assertEqual(self._root('<a b=">"/>'), '<a xmlns="" b=">"/>')

    def test_not_well_formed_input_is_refused(self):
        for text in ("", "text", "<a><b></a>", "<a/><b/>", "<a>\x00</a>", "<p:a/>", "<a>&undefined;</a>", "<a/>junk"):
            with self.subTest(text=text):
                self.assertIsNone(self._root(text))

    def test_entity_declarations_and_lone_surrogates_are_refused(self):
        self.assertIsNone(self._root('<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'))
        self.assertIsNone(self._root("<a>\ud800</a>"))


@mock.patch("apps.extraction.adapters.base.subprocess.run")
class SplicedOutputTests(TestCase):
    """The embedded XML is spliced in as text; it must read back as the tool's own elements."""

    def _post(self, run, stdout, accept="application/premis+xml", query=""):
        run.return_value = _ok(stdout)
        response = APIClient().post(
            "/api/v1/extract/" + query,
            {"file": SimpleUploadedFile("upload.jpg", b"data", content_type="image/jpeg"), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT=accept,
        )
        self.assertEqual(response.status_code, 200)
        return etree.fromstring(response.content)

    def test_output_without_a_namespace_stays_without_one(self, run):
        xml = self._post(run, "<report><a/></report>")
        report = xml.find("p:event//report", NS)
        self.assertIsNotNone(report)
        self.assertEqual(etree.QName(report).namespace, None)

    def test_the_embedded_element_equals_the_parsed_output_whatever_surrounds_it(self, run):
        raw = '<?xml version="1.0"?><!-- c --><x:r xmlns:x="urn:x" a="1"><x:i>t &amp; u</x:i></x:r><!-- tail -->'
        xml = self._post(run, raw, accept="application/premis+xml")
        embedded = xml.find(".//{urn:x}r")
        expected = etree.fromstring(raw.encode())
        self.assertEqual(
            etree.tostring(embedded, method="c14n", exclusive=True), etree.tostring(expected, method="c14n", exclusive=True)
        )

    def test_every_tool_event_gets_its_own_output(self, run):
        run.return_value = _ok("<a><b/></a>")
        response = APIClient().post(
            "/api/v1/extract/",
            {"file": SimpleUploadedFile("f.jpg", b"data", content_type="image/jpeg"), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT="application/premis+xml",
        )
        xml = etree.fromstring(response.content)
        tool_events = len(xml.findall("p:event", NS)) - 1  # the last event is the summary
        self.assertGreaterEqual(tool_events, 1)
        self.assertEqual(len(xml.findall(".//p:eventOutcomeDetailExtension/a/b", NS)), tool_events)


class EmbeddingWithTheIdentificationEventTests(TestCase):
    """The FFIS `format identification` event comes first; each tool's output must still land on its own event."""

    def _document(self, *, ffis, outputs=("<x1/>", "<x2/>")):
        from dataclasses import replace

        from apps.api.renderers import PremisXMLRenderer
        from apps.api.serializers import serialize_extraction_result
        from tests.api.test_premis import _result, _tool

        tools = [replace(_tool(f"t{i}"), raw_output=out, raw_output_format="xml") for i, out in enumerate(outputs, 1)]
        result = replace(
            _result(tools),
            puid="fmt/18",
            mime_type="application/pdf",
            identification_url="https://ffis.example/" if ffis else None,
            ffis_provenance={"outcome": "success"} if ffis else None,
        )
        data = serialize_extraction_result(result, request_id="r", filename="a.pdf")
        return etree.fromstring(PremisXMLRenderer().render(data))

    def _extensions(self, root):
        return [e.find("p:eventOutcomeInformation/p:eventOutcomeDetail/p:eventOutcomeDetailExtension", NS) for e in root.findall("p:event", NS)]

    def test_each_tool_event_has_its_own_output_after_the_identification_event(self):
        root = self._document(ffis=True)
        types = [e.findtext("p:eventType", namespaces=NS) for e in root.findall("p:event", NS)]
        self.assertEqual(types, ["format identification", "metadata extraction", "metadata extraction", "metadata extraction"])

        identification, first, second, summary = self._extensions(root)
        self.assertIsNone(identification)
        self.assertIsNone(summary)
        self.assertEqual([etree.QName(c).localname for c in first], ["x1"])
        self.assertEqual([etree.QName(c).localname for c in second], ["x2"])

    def test_the_output_is_the_same_without_the_identification_event(self):
        root = self._document(ffis=False)
        first, second, summary = self._extensions(root)
        self.assertEqual([etree.QName(c).localname for c in first], ["x1"])
        self.assertEqual([etree.QName(c).localname for c in second], ["x2"])
        self.assertIsNone(summary)

    def test_one_tool_with_the_identification_event(self):
        root = self._document(ffis=True, outputs=("<only/>",))
        extensions = self._extensions(root)
        self.assertEqual(len(extensions), 3)
        self.assertEqual([etree.QName(c).localname for c in extensions[1]], ["only"])

    def test_the_exact_copy_follows_the_same_events(self):
        from dataclasses import replace

        from apps.api.renderers import PremisXMLRenderer
        from apps.api.serializers import serialize_extraction_result
        from tests.api.test_premis import _result, _tool

        tool = replace(_tool("t1"), raw_output="plain text", raw_output_format="text")
        result = replace(_result([tool]), identification_url="https://ffis.example/", ffis_provenance={})
        data = serialize_extraction_result(result, request_id="r", filename="a.pdf")
        root = etree.fromstring(PremisXMLRenderer().render(data))
        raw = [e.find("p:eventOutcomeInformation/p:eventOutcomeDetail/p:eventOutcomeDetailExtension/rawOutput", NS) for e in root.findall("p:event", NS)]
        self.assertIsNone(raw[0])
        self.assertEqual(raw[1].text, "plain text")
        self.assertEqual(raw[1].get("format"), "text")
