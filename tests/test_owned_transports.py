from __future__ import annotations

import pytest
import requests

from torrent_clients.transport.errors import (
    TransportConnectionError,
    TransportProtocolError,
)
from torrent_clients.transport.http import HttpSession
from torrent_clients.transport.qbittorrent import QbittorrentTransport
from torrent_clients.transport.transmission import TransmissionTransport


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        json_data=None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}

    def json(self):  # type: ignore[no-untyped-def]
        if self._json_data is None:
            raise ValueError("no json payload")
        return self._json_data


class _RecordingRequestsSession:
    def __init__(self, responses: list[_Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, timeout=None, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "method": method,
                "url": url,
                "timeout": timeout,
                "kwargs": kwargs,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected request without queued response")
        return self.responses.pop(0)


def test_http_session_applies_default_timeout_and_translates_request_errors() -> None:
    session = HttpSession("http://example.com/", timeout=7)

    class _FailingSession:
        def request(self, method, url, timeout=None, **kwargs):  # type: ignore[no-untyped-def]
            _ = method, url, kwargs
            assert timeout == 7
            raise requests.RequestException("boom")

    session._session = _FailingSession()

    with pytest.raises(TransportConnectionError):
        session.request("GET", "/api/v2/test")


def test_qb_transport_logs_in_once_and_reuses_authenticated_session() -> None:
    raw_session = _RecordingRequestsSession(
        responses=[
            _Response(text="Ok."),
            _Response(json_data=[{"hash": "hash-a", "name": "Demo"}]),
            _Response(json_data=[{"hash": "hash-b", "name": "Other"}]),
        ]
    )
    session = HttpSession("http://qb.local:8080/", timeout=11)
    session._session = raw_session
    transport = QbittorrentTransport("http://qb.local:8080/", "user", "pass", session=session)

    first = transport.torrents_info()
    second = transport.torrents_info()

    assert first == [{"hash": "hash-a", "name": "Demo"}]
    assert second == [{"hash": "hash-b", "name": "Other"}]
    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/torrents/info",
        "http://qb.local:8080/api/v2/torrents/info",
    ]


def test_qb_transport_uses_legacy_resume_pause_endpoints_for_pre_5_web_api() -> None:
    raw_session = _RecordingRequestsSession(
        responses=[
            _Response(text="Ok."),
            _Response(text="2.10.4"),
            _Response(text="Ok."),
            _Response(text="Ok."),
        ]
    )
    session = HttpSession("http://qb.local:8080/", timeout=11)
    session._session = raw_session
    transport = QbittorrentTransport("http://qb.local:8080/", "user", "pass", session=session)

    transport.torrents_start(torrent_hashes=["hash-a"])
    transport.torrents_stop(torrent_hashes=["hash-a"])

    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/app/webapiVersion",
        "http://qb.local:8080/api/v2/torrents/resume",
        "http://qb.local:8080/api/v2/torrents/pause",
    ]


def test_transmission_transport_retries_with_new_session_id_after_409() -> None:
    raw_session = _RecordingRequestsSession(
        responses=[
            _Response(status_code=409, headers={"X-Transmission-Session-Id": "session-2"}),
            _Response(
                status_code=200,
                json_data={"result": "success", "arguments": {"torrents": [{"id": 1}]}},
            ),
        ]
    )
    session = HttpSession("http://tr.local:9091/", timeout=13)
    session._session = raw_session
    transport = TransmissionTransport("http://tr.local:9091/", "user", "pass", session=session)

    torrents = transport.get_torrents(arguments=["id"])

    assert torrents == [{"id": 1}]
    first_headers = raw_session.calls[0]["kwargs"]["headers"]
    second_headers = raw_session.calls[1]["kwargs"]["headers"]
    assert first_headers.get("X-Transmission-Session-Id") == ""
    assert second_headers.get("X-Transmission-Session-Id") == "session-2"


def test_transmission_transport_raises_protocol_error_for_rpc_failure() -> None:
    raw_session = _RecordingRequestsSession(
        responses=[
            _Response(
                status_code=200,
                json_data={"result": "duplicate torrent", "arguments": {}},
            )
        ]
    )
    session = HttpSession("http://tr.local:9091/", timeout=13)
    session._session = raw_session
    transport = TransmissionTransport("http://tr.local:9091/", "user", "pass", session=session)

    with pytest.raises(TransportProtocolError, match="duplicate torrent"):
        transport.get_torrents(arguments=["id"])
