"""Repository-owned downloader transport primitives."""

from torrent_clients.transport.errors import (
    TransportAuthenticationError,
    TransportConnectionError,
    TransportError,
    TransportProtocolError,
    TransportResponseError,
)
from torrent_clients.transport.http import HttpSession

__all__ = [
    "HttpSession",
    "TransportAuthenticationError",
    "TransportConnectionError",
    "TransportError",
    "TransportProtocolError",
    "TransportResponseError",
]
