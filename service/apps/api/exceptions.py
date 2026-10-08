"""Map domain exceptions to HTTP responses (DRF exception handler)."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.extraction.exceptions import ToolNotConfigured


def exception_handler(exc, context):
    if isinstance(exc, ToolNotConfigured):
        return Response(
            {"errors": [str(exc)]}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    return drf_exception_handler(exc, context)
