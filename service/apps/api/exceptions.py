"""Map domain exceptions to HTTP responses (DRF exception handler)."""

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.extraction.exceptions import ToolNotConfigured


logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    if isinstance(exc, ToolNotConfigured):
        return Response(
            {"errors": [str(exc)]}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unexpected failure: return the error in the API's own shape rather than an HTML 500
        # page (MES-REQ-5-001), without leaking internals.
        logger.exception("Unhandled error during extraction request", exc_info=exc)
        return Response(
            {"errors": ["An unexpected error occurred while processing the request."]},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return response
