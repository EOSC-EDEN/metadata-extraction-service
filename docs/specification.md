# Metadata Extraction Service Specification

## Abstract
To improve and support preservation planning, file migration, and other preservation and access operations, the need was identified for a service which extracts metadata. 

## Authors

## Status of this document
This document is a proposed specification. It is currently a draft and open for review by EDEN technical working groups.

## Rationale
The Metadata Extraction Service provides standardised, interoperable metadata describing the properties of digital files. This metadata is essential for supporting digital preservation activities, including preservation planning, file format transformation, file repair, creation of derivatives, and many other domain-specific operations. Because metadata requirements  differ across file types, specialised metadata standards exist for different domains (e.g., NISOIMG (MIX) for images and audioMD for audio content). In most cases though, metadata extractors do not use standardised metadata schemes and develop their own output format.

No single extraction tool can capture all relevant internal metadata for the diverse range of file formats managed by a Trusted Digital Archive (TDA). The Metadata Extraction Service therefore aggregates the output from multiple extraction tools, selecting those appropriate for each supported file format. The service aggregates  the extracted metadata, and returns the results in a standardised metadata container. The top-level structure of this container is standardised, while the extracted metadata within it may be represented in different formats, such as XML or JSON, depending on the extraction tool used. This approach ensures interoperability and consistent consumption by downstream preservation services.

By using this service, repositories and Trusted Digital Archives benefit from a consistent approach to metadata extraction. Enhancing the efficiency and reliability of preservation workflows supports better decision-making for preservation actions, and promotes interoperability and data exchange across systems and institutions.


## Introduction
Digital preservation relies on accurate and consistent metadata to enable repositories and Trusted Digital Archives (TDAs) to identify, understand, manage, and preserve digital objects over time. Internal metadata provides information about the properties of digital files, supporting preservation activities such as validation, risk assessment, transformation, creation of derivatives, and repair. 

The need for a standardised Metadata Extraction Service was identified to address these challenges. Existing repositories typically rely on different extraction tools and metadata schemas, making it difficult to achieve interoperability between systems and to support automated preservation workflows. Furthermore, no single extraction tool is capable of extracting all relevant internal metadata for every supported file format, requiring repositories to combine multiple tools while managing differences in their outputs.

This specification defines a Metadata Extraction Service that is able to aggregate the output of multiple metadata extraction tools, and return their outputs in a way that can be consumed by existing digital preservation systems by using a standardised metadata container.. By providing a standardised and interoperable interface for technical metadata extraction, the specification enables consistent metadata extraction across repositories and preservation platforms. 

This specification also describes that the tool used or available for metadata extraction must be exposed, so users of the service can make a decision if the service is suitable for their data type.

This supports the identified need for interoperable preservation services, reduces implementation effort for repositories, and facilitates the reliable exchange and reuse of metadata within the EOSC EDEN ecosystem.

The Metadata Extraction Service realises the functionality required to support the Core Preservation Process CPP-009 – Metadata Extraction, which extracts the properties of digital files and records the resulting metadata. Within the preservation workflow, this process depends on file format identification (CPP-008) to select the appropriate extraction tools and is itself a prerequisite for several downstream preservation processes, including Data Quality Assessment (CPP-019), Risk Definition and Extraction (CPP-023), File Migration (CPP-014), File Repair (CPP-027), Metadata Ingest and Management (CPP-016), and Ingest (CPP-029). By standardising the implementation of this service, the specification promotes interoperability while ensuring that the outputs of CPP-009 can be consistently consumed by other preservation services throughout the digital preservation lifecycle.


## Scope
This specification defines interfaces, and functional requirements of the Metadata Extraction Service within the EOSC EDEN preservation ecosystem. The service realises the functionality of Core Preservation Process (CPP)-009 – Metadata Extraction by providing a standardised mechanism for extracting, and exposing internal metadata from digital files in interoperable formats.

Within EOSC EDEN, the Metadata Extraction Service will be developed in alignment with the KU Leuven File Format Service architecture, which is intended to support the distributed sharing of file format information and preservation knowledge This specification therefore targets an implementation that integrates the already existing File Format Library, make use of existing tool registries, such as COPTR.

A central objective of this specification is the definition of an integration layer that enables multiple metadata extraction tools to be incorporated behind a common service interface, in combination with the File Format Service Library made available by KU Leuven. Since no single extraction tool can provide comprehensive technical metadata for every supported file format, the integration layer will support the selection, invocation, configuration, and orchestration of appropriate extraction tools, while harmonising their outputs into standardised metadata representations, while also addressing extraction challenges faced by the disciplines.

The service should also make it possible to extract all possible metadata of Information Packages, specifically Submission Information Packages and Archival Information Packages. In order to make this possible, the service will be able to use the specification[^1] created for the SIP2AIP Factory Service to find the metadata files within the package, which type of metadata it contains, and the payload files themselves.

This specification does not define new metadata extraction tools or technical metadata standards. Instead, it specifies how existing tools can be integrated, orchestrated, and exposed through a common service interface to support interoperable metadata extraction within the EOSC EDEN preservation ecosystem.

In scope:
- Add additional information to the Format Library in relation to metadata extraction tools
- Develop a wrapper to extract metadata from a file
- Implement Exiftool & MediaInfo as extraction tools in the wrapper
- Develop an API for the wrapper

[^1]: https://github.com/EOSC-EDEN/wp2-oais-packaging-information

## Core-preservation processes
The Metadata Extraction Service implements the functionality defined by CPP-009 – Metadata Extraction. Its primary responsibility is to extract internal metadata from digital files by invoking one or more metadata extraction tools, harmonising their outputs, and producing standardised metadata that can be recorded within the archival information package. The service enables repositories to identify the properties of digital objects which are essential for preservation planning and subsequent preservation actions.
To perform metadata extraction, the service depends on CPP-008 – File Format Identification. The file format identifier produced by CPP-008 is used to determine the most appropriate metadata extraction tool, or combination of tools, for the digital object being processed. The service also considers requirements originating from CPP-019 – Data Quality Assessment, CPP-022 – Significant Properties Definition, and CPP-023 – Risk Definition and Extraction, which define the metadata properties that need to be extracted for a particular preservation context.
The technical metadata produced by the service is subsequently consumed by several other Core Preservation Processes. CPP-014 – File Migration, CPP-026 – File Normalisation, CPP-027 – File Repair, and CPP-028 – Creation of Derivatives rely on detailed technical metadata to correctly process digital objects and to verify that preservation actions have produced the intended results. Metadata generated by the service is also used by CPP-019 – Data Quality Assessment, to evaluate the quality of digital objects, and by CPP-023 – Risk Definition and Extraction, to identify preservation risks based on file characteristics.
As part of the ingest workflow, the Metadata Extraction Service supports CPP-029 – Ingest, by generating the metadata that is recorded within the Information Package. The service may also support CPP-010 – File Format Validation, by providing additional properties required to select the appropriate validation tool when file format identification alone is insufficient. Furthermore, the provenance information generated during metadata extraction can contribute to CPP-021 – AIP Versioning, ensuring that preservation actions remain traceable over time.
Within the EOSC EDEN architecture, the Metadata Extraction Service therefore serves as a foundational service that connects file format identification with downstream preservation services. By exposing metadata extraction through a standardised service interface, it enables multiple Core Preservation Processes to reuse a common capability, while ensuring consistent, interoperable metadata across repositories.


## Conformance
The keywords MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be interpreted as described in RFC 2119 (https://www.rfc-editor.org/rfc/rfc2119).

## Normative requirements

### Requirement Group 1 – Integration of service
- {{ MES-REQ-1-001 }} The service MUST be able to set a default tool to extract metadata from submitted files
- {{ MES-REQ-1-002 }} The service SHOULD make use of one or more Format Libraries[^2] to determine which metadata extraction tool to use
- {{ MES-REQ-1-003 }} The service MAY be able to extract metadata based on the SIP & AIP Specification of the SIP2AIP Factory[^3]
- {{ MES-REQ-1-004 }} The service MUST support mapping an identified file-format identifier, including a PRONOM PUID and/or mime types where available, to one or more configured metadata extraction tools.
- {{ MES-REQ-1-005 }} The service MUST support the invocation of local cli and/or (externally) hosted services to extract metadata

[^2]: https://github.com/libis/teneo-format_library
[^3]: https://github.com/EOSC-EDEN/wp2-oais-packaging-information


### Requirement Group 2 – Interfaces
- {{ MES-REQ-2-001 }} The service MUST expose a REST API interface
- {{ MES-REQ-2-002 }} The interface MUST be able to extract metadata from a single file per request
- {{ MES-REQ-2-003 }} The interface MUST be able to extract metadata from a single submitted Information Package, aligned with the SIP & AIP Specification[^4]
- {{ MES-REQ-2-004 }} The service SHOULD be able to be used via a user friendly interface
- {{ MES-REQ-2-005 }} The API MUST return the result in XML and JSON format following a schema
- {{ MES-REQ-2-006 }} The service MUST be able to be used by a simple UI
- {{ MES-REQ-2-006 }} The API SHOULD be able to accept a POID or mime type to select the extraction tool

[^4]: https://github.com/EOSC-EDEN/wp2-oais-packaging-information

### Requirement Group 3 – Extraction of metadata
- {{ MES-REQ-3-001 }} The service MUST be able to extract technical metadata from an individual file
- {{ MES-REQ-3-002 }} The service MUST be able to extract provenance metadata from an individual file
- {{ MES-REQ-3-003 }} The service MUST be able to invoke one or more metadata extraction tools for an individual file, in an order determined by configurable tool-selection and sequencing rules.
- {{ MES-REQ-3-004 }} The service MUST return the metadata produced by each metadata extraction tool in its original (raw) form, without modification
- {{ MES-REQ-3-005 }} The service MAY label extracted metadata with its metadata type and the metadata model or standard to which it conforms.
- {{ MES-REQ-3-006 }} Failure of one metadata extraction tool MUST NOT automatically prevent the results of successfully completed tools from being returned, unless this has been configured in the tool as mandatory.
- {{ MES-REQ-3-007 }} The output MUST identify which metadata extraction tool produced each metadata element or raw result.
- {{ MES-REQ-3-008 }} The output MUST record the execution status of each invoked metadata extraction tool.
- {{ MES-REQ-3-009 }} The output MUST identify which Format Library(s) were used to select the tools used for metadata extraction.
- {{ MES-REQ-3-010 }} The service MAY be able to invoke a File Identification Service, if no POID or mime type has been supplied when invoking the service

### Requirement Group 4 – Wrapper
- {{ MES-REQ-4-001 }} The wrapper MUST integrate the tool ExifTool
- {{ MES-REQ-4-002 }} The wrapper MUST integrate the tool MediaInfo
- {{ MES-REQ-4-003 }} The wrapper MUST integrate tool FFprobe
- {{ MES-REQ-4-004 }} The wrapper MUST integrate tool File Scraper
- {{ MES-REQ-4-005 }} The wrapper MUST integrate tool ImageMagick
- {{ MES-REQ-4-006 }} The wrapper MUST integrate tool Apache Tika
- {{ MES-REQ-4-007 }} The wrapper MUST allow additional metadata extraction tools to be configured and integrated without requiring changes to the wrapper's core functionality

### Requirement Group 5 – Errors & Warnings
- {{ MES-REQ-5-001 }} The wrapper and API interface MUST be able to return the errors and warnings logged during extraction back to the user.


## Non-normative Guidance
**Tool selection and configuration**

Metadata extraction tools should preferably be selected based on the identified file format, using information maintained in the File Format Library. A file format may be associated with multiple extraction tools where complementary metadata can be obtained. The order in which tools are executed may be configurable and may depend on the file format, preservation context, or metadata requirements. This follows the specification's model in which the service uses file-format identification and the Format Library to determine appropriate extraction tools.

**Integration of extraction tools**

Tool-specific functionality should be isolated from the core wrapper implementation, for example through adapters or plugins. Each adapter can be responsible for invoking a particular extraction tool, supplying configuration parameters, interpreting its execution status, and returning its output to the service. This makes it easier to add additional extraction tools, without modifying the core orchestration logic, in line with MES-REQ-4-003.

**Preservation of raw metadata**

Where possible, the original output produced by each extraction tool should be retained. Keeping the original tool output provides traceability and allows future consumers to reinterpret the metadata without depending exclusively on transformations performed by the service. The specification already requires the raw results to be returned without modification and to identify the producing tool.

**Harmonisation and standardised metadata**

Raw tool output and harmonised metadata should be treated as separate representations. Implementations may map extracted properties to recognised technical metadata schemas, where suitable mappings exist, while retaining the original output alongside the harmonised representation. This supports the stated objective of harmonising tool outputs and producing interoperable metadata, while acknowledging that extraction tools commonly use their own output formats.

**Failure handling**

When multiple tools are invoked, implementations should preferably isolate failures so that failure of an optional extraction tool does not discard metadata successfully produced by other tools. Configuration may distinguish mandatory tools from supplementary tools. Diagnostics should provide enough information to identify which tool failed and why. This reflects MES-REQ-3-008 and the error-handling requirements.

**Archivematica integration**

For Archivematica deployments, the Metadata Extraction Service should preferably appear as a single generic characterisation capability. Tool selection should remain internal to the service rather than requiring individual Archivematica characterisation commands for ExifTool, MediaInfo, or other tools.

**Extensibility**

Implementations should avoid hard-coding assumptions about ExifTool and MediaInfo. These tools provide an initial implementation, but the architecture should accommodate specialised extraction tools for particular file formats as they are added to the File Format Library. This is particularly important because the specification explicitly recognises that no single extraction tool can capture all relevant metadata across all file formats.

## Traceability to EOSC EDEN Requirements
### QUALITY-TECHNICAL-REQ-016 (Mandatory)
*Significant properties must be able to be recorded as technical metadata. Addressed by: MES-REQ-3-001*