"""Shared HTTP session wrapper for repository-owned transports."""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urljoin

import requests

from torrent_clients.transport.errors import (
    TransportConnectionError,
    TransportProtocolError,
    TransportResponseError,
)


class HttpSession:
    """Thin wrapper around ``requests.Session`` with explicit timeouts."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        normalized_base = base_url.rstrip("/") + "/"
        self.base_url = normalized_base
        self.timeout = timeout
        self._session = session or requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int | Iterable[int] | None = None,
        **kwargs,
    ) -> requests.Response:
        timeout = kwargs.pop("timeout", self.timeout)
        try:
            response = self._session.request(
                method,
                self._url(path),
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise TransportConnectionError(str(exc)) from exc

        if expected_status is None:
            return response

        if isinstance(expected_status, int):
            allowed_statuses = {expected_status}
        else:
            allowed_statuses = set(expected_status)

        if response.status_code not in allowed_statuses:
            raise TransportResponseError(
                f"unexpected HTTP status {response.status_code} for {method.upper()} {path}"
            )
        return response

    def request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: int | Iterable[int] | None = None,
        **kwargs,
    ):
        response = self.request(
            method,
            path,
            expected_status=expected_status,
            **kwargs,
        )
        try:
            return response.json()
        except ValueError as exc:
            raise TransportProtocolError(
                f"invalid JSON response for {method.upper()} {path}"
            ) from exc
