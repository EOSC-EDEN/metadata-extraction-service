# The persistence boundary

MES returns results and stores nothing durable. Everything it keeps is temporary: uploads while they are processed, and job state until its TTL expires.

The caller records the response (raw tool output, tool versions, timestamp, errors and warnings) in the Information Package and hands it on to AIP Versioning (CPP-021). The PREMIS `metadata extraction` event in the result is ready to copy into a PREMIS record, but writing it into the package is the caller's job.

## Why

- A preservation service that owns long-term storage must also own its integrity, access control and migration. MES does one thing, extraction, and leaves storage to the archive that already does those things.
- The same file extracted twice with different tool versions gives different output. Keeping that history is a decision for the archive's provenance model, not for the extractor.
- Stateless processing keeps the service easy to scale and replace. The only state, job records, is deliberately short-lived (`MES_JOB_TTL_SECONDS`).

See "Recording and persistence" in the [specification](../../specification.md), and [Set limits and purge jobs](../how-to/limits-and-purging.md) for the temporary state.
