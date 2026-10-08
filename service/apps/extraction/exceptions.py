class ToolNotConfigured(Exception):
    """No usable tool configuration/adapter exists for what the orchestrator needs."""


class FFISUnavailable(Exception):
    """The FFIS HTTP call itself failed (network, timeout, HTTP error, malformed payload).

    Distinct from FFIS successfully answering "failure"/no identifier, which
    `FFISClient.identify()` reports as `None`.
    """
