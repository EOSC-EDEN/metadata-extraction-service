"""The standalone `premis:premis` document (`Accept: application/premis+xml` or `?format=premis`)."""

import base64
import hashlib
import subprocess
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import PREMIS_NAMESPACE
from tests.api.test_raw_output_embedding import RDF
from tests.api.premis_schema import premis_xsd

P = {"p": PREMIS_NAMESPACE}
XSI = "http://www.w3.org/2001/XMLSchema-instance"
MEDIA_TYPE = "application/premis+xml"


def _upload(name="upload.jpg", content=b"data"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _premis_xsd():
    return premis_xsd()


@mock.patch("apps.extraction.adapters.base.subprocess.run")
class PremisDocumentTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _ok(self, run, stdout=RDF):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    def _extract(self, **kwargs):
        return self.client.post(
            "/api/v1/extract/", {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart", **kwargs
        )

    def test_accept_header_gives_a_valid_premis_document(self, run):
        self._ok(run)
        response = self._extract(HTTP_ACCEPT=MEDIA_TYPE)

        self.assertEqual(response.status_code, 200)
        self.assertIn(MEDIA_TYPE, response["Content-Type"])
        root = etree.fromstring(response.content)
        self.assertEqual(etree.QName(root).text, f"{{{PREMIS_NAMESPACE}}}premis")
        self.assertEqual(root.get("version"), "3.0")
        self.assertEqual(
            root.get(f"{{{XSI}}}schemaLocation"),
            f"{PREMIS_NAMESPACE} https://www.loc.gov/standards/premis/premis.xsd",
        )
        xsd = _premis_xsd()
        self.assertTrue(xsd.validate(root), xsd.error_log)

    def test_the_document_holds_the_object_then_the_events_then_the_agents(self, run):
        self._ok(run)
        root = etree.fromstring(self._extract(HTTP_ACCEPT=MEDIA_TYPE).content)

        names = [etree.QName(c).localname for c in root]
        self.assertEqual(names[0], "object")
        self.assertEqual(names.count("object"), 1)
        self.assertEqual(sorted(set(names[1:]), key=names.index), ["event", "agent"])
        self.assertEqual(names, sorted(names, key=["object", "event", "agent"].index))
        self.assertEqual(root[0].get(f"{{{XSI}}}type"), "premis:file")

    def test_every_event_is_linked_by_the_object_and_every_agent_by_an_event(self, run):
        self._ok(run)
        root = etree.fromstring(self._extract(HTTP_ACCEPT=MEDIA_TYPE).content)

        events = {e.text for e in root.iterfind("p:event/p:eventIdentifier/p:eventIdentifierValue", P)}
        linked = {e.text for e in root.iterfind("p:object/p:linkingEventIdentifier/p:linkingEventIdentifierValue", P)}
        self.assertEqual(events, linked)
        agents = {
            e.findtext("p:agentIdentifierValue", namespaces=P)
            for e in root.iterfind("p:agent/p:agentIdentifier", P)
            if e.findtext("p:agentIdentifierType", namespaces=P) == "UUID"
        }
        used = {e.text for e in root.iterfind("p:event/p:linkingAgentIdentifier/p:linkingAgentIdentifierValue", P)}
        self.assertEqual(agents, used)

    def test_the_raw_output_and_vocabulary_attributes_are_in_the_document(self, run):
        self._ok(run)
        root = etree.fromstring(self._extract(HTTP_ACCEPT=MEDIA_TYPE).content)

        rdf = root.find(".//p:eventOutcomeDetailExtension/{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF", P)
        self.assertIsNotNone(rdf)
        outcome = root.find(".//p:eventOutcome", P)
        self.assertEqual(outcome.get("authority"), "eventOutcome")

    def test_the_format_query_parameter_selects_it(self, run):
        self._ok(run)
        response = self._extract(**{"QUERY_STRING": "format=premis"})
        self.assertIn(MEDIA_TYPE, response["Content-Type"])
        self.assertEqual(etree.QName(etree.fromstring(response.content)).localname, "premis")

    def test_a_failed_run_is_a_valid_document_with_the_error(self, run):
        run.side_effect = OSError("boom")
        root = etree.fromstring(self._extract(HTTP_ACCEPT=MEDIA_TYPE).content)

        xsd = _premis_xsd()
        self.assertTrue(xsd.validate(root), xsd.error_log)
        notes = [n.text for n in root.findall(".//p:eventOutcomeDetailNote", P)]
        self.assertTrue(any(n.startswith("error: ") for n in notes))
        self.assertTrue(any(n.startswith("error type: ") for n in notes))
        self.assertIsNone(root.find(".//p:eventOutcomeDetailExtension", P))

    def test_json_stays_the_default(self, run):
        self._ok(run)
        response = self._extract()
        self.assertIn("application/json", response["Content-Type"])


RAW_NS = {"p": PREMIS_NAMESPACE}


@mock.patch("apps.extraction.adapters.base.subprocess.run")
class ExactRawOutputTests(TestCase):
    """`?raw=exact` adds the unmodified output next to the parsed copy (MES-REQ-3-004)."""

    def _document(self, run, stdout, query="format=premis&raw=exact"):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        response = APIClient().post(
            "/api/v1/extract/",
            {"file": _upload(), "mime_type": "image/jpeg"},
            format="multipart",
            QUERY_STRING=query,
        )
        self.assertEqual(response.status_code, 200)
        root = etree.fromstring(response.content)
        xsd = _premis_xsd()
        self.assertTrue(xsd.validate(root), xsd.error_log)
        return root

    def _exact(self, root):
        return root.find(".//p:eventOutcomeDetailExtension/rawOutput", RAW_NS)

    def _decoded(self, node):
        text = node.text or ""
        return base64.b64decode(text).decode("utf-8") if node.get("encoding") == "base64" else text

    def test_the_exact_text_comes_with_the_parsed_copy_and_its_checksum(self, run):
        root = self._document(run, RDF)

        extension = root.find(".//p:eventOutcomeDetailExtension", RAW_NS)
        self.assertIsNotNone(extension.find("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"))
        node = self._exact(root)
        self.assertEqual((node.get("format"), self._decoded(node)), ("xml", RDF))
        self.assertEqual(node.get("sha256"), hashlib.sha256(RDF.encode()).hexdigest())

    def test_it_is_only_added_for_raw_exact(self, run):
        self.assertEqual(self._decoded(self._exact(self._document(run, RDF))), RDF)
        for query in ("format=premis", "format=premis&raw=parsed", "format=premis&raw=other", "format=premis&raw="):
            with self.subTest(query=query):
                self.assertIsNone(self._exact(self._document(run, RDF, query)))

    def test_without_it_output_that_is_not_xml_keeps_its_text_but_has_no_checksum(self, run):
        node = self._exact(self._document(run, '{"a": 1}', "format=premis"))
        self.assertEqual((node.text, node.get("sha256")), ('{"a": 1}', None))

    def test_non_xml_output_is_exact_and_gets_a_checksum(self, run):
        root = self._document(run, '{"a": 1}')
        self.assertEqual(self._decoded(self._exact(root)), '{"a": 1}')
        self.assertEqual(self._exact(root).get("sha256"), hashlib.sha256(b'{"a": 1}').hexdigest())

    def test_a_carriage_return_survives_because_the_text_is_base64(self, run):
        raw = "<a>\r\n<b/>\r\n</a>"
        root = self._document(run, raw)

        node = self._exact(root)
        self.assertEqual(node.get("encoding"), "base64")
        self.assertEqual(self._decoded(node), raw)

    def test_characters_xml_cannot_carry_survive_as_base64(self, run):
        raw = "<a>\x00</a>"
        node = self._exact(self._document(run, raw))
        self.assertEqual((node.get("encoding"), self._decoded(node)), ("base64", raw))

    def test_empty_output_has_the_checksum_of_nothing(self, run):
        node = self._exact(self._document(run, ""))
        self.assertEqual(node.get("sha256"), hashlib.sha256(b"").hexdigest())

    def test_application_xml_is_still_not_acceptable_with_the_parameter(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=RDF, stderr="")
        response = APIClient().post(
            "/api/v1/extract/",
            {"file": _upload(), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT="application/xml",
            QUERY_STRING="raw=exact",
        )
        self.assertEqual(response.status_code, 406)
        self.assertIn("application/json", response["Content-Type"])


class FfisAgentDocumentTests(TestCase):
    def test_a_result_identified_by_ffis_is_a_valid_document_with_the_agent(self):
        from dataclasses import replace

        from apps.api.renderers import PremisXMLRenderer
        from apps.api.serializers import serialize_extraction_result
        from tests.api.test_premis import _result, _tool

        result = replace(
            _result([_tool("exiftool")]), puid="fmt/43", mime_type="image/jpeg", identification_url="http://ffis.example:8001"
        )
        data = serialize_extraction_result(result, request_id="r", filename="a.jpg")
        root = etree.fromstring(PremisXMLRenderer().render(data))

        xsd = _premis_xsd()
        self.assertTrue(xsd.validate(root), xsd.error_log)
        names = [e.text for e in root.iterfind("p:agent/p:agentName", P)]
        self.assertEqual(names[1], "File Format Identification Service (FFIS)")
        events = root.findall("p:event", P)
        self.assertEqual([e.findtext("p:eventType", namespaces=P) for e in events], ["format identification", "metadata extraction", "metadata extraction"])
        detail = events[0].findtext("p:eventDetailInformation/p:eventDetail", namespaces=P)
        self.assertEqual(detail, "puid: fmt/43; mime_type: image/jpeg; FFIS: http://ffis.example:8001")
        ffis_id = root.findall("p:agent", P)[1].findtext("p:agentIdentifier/p:agentIdentifierValue", namespaces=P)
        self.assertEqual(events[0].findtext("p:linkingAgentIdentifier/p:linkingAgentIdentifierValue", namespaces=P), ffis_id)
        linked = [e.text for e in root.iterfind("p:object/p:linkingEventIdentifier/p:linkingEventIdentifierValue", P)]
        self.assertIn(events[0].findtext("p:eventIdentifier/p:eventIdentifierValue", namespaces=P), linked)


class ObjectFixityDocumentTests(TestCase):
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_the_document_has_the_digest_and_size_in_schema_order(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=RDF, stderr="")
        response = APIClient().post(
            "/api/v1/extract/",
            {"file": _upload(content=b"hello"), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT=MEDIA_TYPE,
        )
        root = etree.fromstring(response.content)

        xsd = _premis_xsd()
        self.assertTrue(xsd.validate(root), xsd.error_log)
        characteristics = root.find("p:object/p:objectCharacteristics", P)
        self.assertEqual([etree.QName(c).localname for c in characteristics], ["fixity", "size", "format"])
        self.assertEqual(characteristics.findtext("p:size", namespaces=P), "5")
        digest = characteristics.find("p:fixity/p:messageDigestAlgorithm", P)
        self.assertEqual((digest.text, digest.get("authority")), ("SHA-256", "cryptographicHashFunctions"))
        self.assertEqual(
            characteristics.findtext("p:fixity/p:messageDigest", namespaces=P), hashlib.sha256(b"hello").hexdigest()
        )
