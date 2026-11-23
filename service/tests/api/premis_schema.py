"""The official PREMIS 3.0 schema (a test fixture, see `tests/fixtures/README.md`)."""

from pathlib import Path

from lxml import etree

PREMIS_XSD = Path(__file__).resolve().parents[1] / "fixtures" / "premis.xsd"


def premis_xsd() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(PREMIS_XSD)))
