from __future__ import annotations


class Error(Exception):
    """Base exception class for all proctrace errors."""


class ProctraceError(Error):
    """General proctrace operational error."""


class ConfigurationError(Error):
    """Raised when an invalid configuration or argument is provided."""


class ProcessNotFoundError(Error, ProcessLookupError):
    """Raised when a target process does not exist or has already terminated.

    Inherits from both `Error` and `ProcessLookupError` so existing callers
    catching `ProcessLookupError` remain fully compatible.
    """


class SignalHandlingError(Error):
    """Raised when registering or processing OS signals fails."""


class ResourceMonitoringError(Error):
    """Raised when resource sampling or measurement encounters a failure."""


__all__ = [
    "ConfigurationError",
    "Error",
    "ProcessNotFoundError",
    "ProctraceError",
    "ResourceMonitoringError",
    "SignalHandlingError",
]
