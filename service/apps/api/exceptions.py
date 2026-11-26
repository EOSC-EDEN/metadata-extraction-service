"""Map domain exceptions to HTTP responses (DRF exception handler).

Every error body has one shape: {"errors": [str, ...], "request_id": str}
(MES-REQ-5-001).
"""

import logging

from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.extraction.exceptions import ToolNotConfigured


logger = logging.getLogger(__name__)


def _use_json(request):
    """Answer with JSON whatever was negotiated: an error is never a PREMIS document."""
    if request is not None:
        request.accepted_renderer = JSONRenderer()
        request.accepted_media_type = JSONRenderer.media_type


def exception_handler(exc, context):
    _use_json(context.get("request"))
    request_id = getattr(context.get("request"), "mes_request_id", None)
    extra = {"request_id": request_id}

    if isinstance(exc, ToolNotConfigured):
        logger.error("tool not configured: %s", exc, extra=extra)
        return _error_response([str(exc)], status.HTTP_503_SERVICE_UNAVAILABLE, request_id)
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unexpected failure: return the error in the API's own shape rather than an HTML 500
        # page, without leaking internals.
        logger.exception("Unhandled error during extraction request", exc_info=exc, extra=extra)
        return _error_response(
            ["An unexpected error occurred while processing the request."],
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id,
        )
    logger.warning(
        "request rejected (%s) %s: %s",
        response.status_code,
        _describe(context.get("request"), response.status_code),
        exc,
        extra=extra,
    )
    response.data = {"errors": _flatten(response.data), "request_id": request_id}
    return response


_MAX_LOGGED_HEADER = 200


def _describe(request, status_code) -> str:
    """`METHOD /path`, plus `[Accept: ...]` for a 406, so a rejected request can be traced to its client.

    The path is logged without its query string, and no body or other header is, because they can hold
    identifiers. The Accept value is cut and has its control characters escaped, so a client cannot use it
    to fill or forge log lines.
    """
    if request is None:
        return "-"
    description = f"{request.method} {request.path}"
    if status_code == status.HTTP_406_NOT_ACCEPTABLE:
        accept = request.META.get("HTTP_ACCEPT")
        shown = "(none)" if accept is None else accept[:_MAX_LOGGED_HEADER].encode("unicode_escape").decode("ascii")
        description += f" [Accept: {shown}]"
    return description


def _error_response(errors, status_code, request_id):
    return Response({"errors": errors, "request_id": request_id}, status=status_code)


def _flatten(data, prefix="") -> list[str]:
    """Reduce DRF error data ({"file": ["..."]}, {"detail": "..."}, lists) to a list of strings."""
    if isinstance(data, dict):
        messages = []
        for key, value in data.items():
            child_prefix = prefix if key in ("detail", "non_field_errors") else f"{prefix}{key}: "
            messages.extend(_flatten(value, child_prefix))
        return messages
    if isinstance(data, (list, tuple)):
        messages = []
        for item in data:
            messages.extend(_flatten(item, prefix))
        return messages
    return [f"{prefix}{data}"]
