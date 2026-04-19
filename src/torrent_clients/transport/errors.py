"""Repository-owned transport error types."""

from __future__ import annotations


class TransportError(RuntimeError):
    """Base class for downloader transport failures."""


class TransportConnectionError(TransportError):
    """Raised when an HTTP request cannot be completed."""


class TransportAuthenticationError(TransportError):
    """Raised when downloader authentication fails."""


class TransportProtocolError(TransportError):
    """Raised when a downloader responds with an unexpected protocol payload."""


class TransportResponseError(TransportProtocolError):
    """Raised when a downloader returns an unexpected HTTP status."""
