"""FFISClient: identify a file's format via the EOSC-EDEN FFIS.

Uses `POST /identify` (by-value multipart upload), since MES and FFIS don't
necessarily share a filesystem. The by-reference `POST /identify/path` mode
is a future option for co-located deployments.

The client only reports a resolution, `None` (FFIS answered but couldn't
identify the file) or raises `FFISUnavailable`. Fallback policy belongs to
`tool_resolution.py`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import requests
from django.conf import settings

from apps.extraction.exceptions import FFISUnavailable


@dataclass(frozen=True)
class FFISResolution:
    puid: str | None
    mime_type: str | None
    outcome: str
    engine_warnings: list[str] = field(default_factory=list)
    #: FFIS's own `provenance` block (per-engine results), kept for traceability.
    provenance: dict = field(default_factory=dict)


class FFISClient:
    def __init__(self, base_url=None, timeout_seconds=None):
        self.base_url = settings.MES_FFIS_BASE_URL if base_url is None else base_url
        self.timeout_seconds = (
            settings.MES_FFIS_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )

    def identify(self, file_path) -> FFISResolution | None:
        payload = self._post_identify(file_path)
        try:
            return self._parse(payload)
        except (AttributeError, TypeError, KeyError) as exc:
            raise FFISUnavailable(f"Malformed FFIS response: {exc}") from exc

    def _post_identify(self, file_path) -> dict:
        if not self.base_url:
            raise FFISUnavailable("MES_FFIS_BASE_URL is not configured.")
        url = f"{self.base_url.rstrip('/')}/identify"
        try:
            with open(file_path, "rb") as handle:
                response = requests.post(
                    url,
                    files={"file": (Path(file_path).name, handle)},
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, OSError, ValueError) as exc:
            raise FFISUnavailable(f"FFIS request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise FFISUnavailable("FFIS response was not a JSON object.")
        return payload

    def _parse(self, payload) -> FFISResolution | None:
        provenance = payload.get("provenance") or {}
        outcome = provenance.get("outcome")
        identifiers = payload.get("identifiers") or []
        if outcome == "failure" or not identifiers:
            return None

        puid = self._first_value(identifiers, settings.MES_FFIS_PUID_SCHEMES)
        mime_type = self._first_value(identifiers, settings.MES_FFIS_MIME_SCHEMES)
        if puid is None and mime_type is None:
            # No scheme matched: fall back to the primary identifier. Heuristic
            # until scheme strings are confirmed: a "/" means MIME, otherwise PUID.
            primary = (payload.get("primary_identifier") or {}).get("value")
            if primary:
                if "/" in primary:
                    mime_type = primary
                else:
                    puid = primary
        if puid is None and mime_type is None:
            return None

        return FFISResolution(
            puid=puid,
            mime_type=mime_type,
            outcome=outcome,
            engine_warnings=self._collect_warnings(payload, provenance),
            provenance=provenance,
        )

    @staticmethod
    def _first_value(identifiers, schemes):
        wanted = {scheme.lower() for scheme in schemes}
        for identifier in identifiers:
            if str(identifier.get("scheme", "")).lower() in wanted and identifier.get("value"):
                return identifier["value"]
        return None

    @staticmethod
    def _collect_warnings(payload, provenance) -> list[str]:
        messages = []
        for warning in payload.get("warnings") or []:
            if isinstance(warning, dict):
                messages.append(str(warning.get("message") or warning))
            else:
                messages.append(str(warning))
        for tool in provenance.get("tools") or []:
            if tool.get("error"):
                messages.append(f"FFIS engine '{tool.get('name')}': {tool['error']}")
        return messages
