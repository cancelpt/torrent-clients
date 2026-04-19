from __future__ import annotations

import base64

import pytest

from torrent_clients.transport.errors import TransportAuthenticationError, TransportProtocolError
from torrent_clients.transport.http import HttpSession
from torrent_clients.transport.transmission import TransmissionTransport


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        json_data=None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):  # type: ignore[no-untyped-def]
        if self._json_data is None:
            raise ValueError("no json")
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


def _transport_with_raw_session(
    responses: list[_Response],
    *,
    timeout: float = 17.0,
) -> tuple[TransmissionTransport, _RecordingRequestsSession]:
    raw_session = _RecordingRequestsSession(responses)
    session = HttpSession("http://tr.local:9091/", timeout=13)
    session._session = raw_session
    transport = TransmissionTransport(
        "http://tr.local:9091/",
        "user",
        "pass",
        timeout=timeout,
        session=session,
    )
    return transport, raw_session


def test_get_torrents_retries_with_updated_session_id_on_409() -> None:
    transport, raw_session = _transport_with_raw_session(
        [
            _Response(status_code=409, headers={"X-Transmission-Session-Id": "sid-2"}),
            _Response(
                status_code=200,
                json_data={"result": "success", "arguments": {"torrents": [{"id": 9}]}},
            ),
        ],
        timeout=11.0,
    )

    torrents = transport.get_torrents(arguments=["id"])

    assert torrents == [{"id": 9}]
    first_headers = raw_session.calls[0]["kwargs"]["headers"]
    second_headers = raw_session.calls[1]["kwargs"]["headers"]
    assert first_headers["X-Transmission-Session-Id"] == ""
    assert second_headers["X-Transmission-Session-Id"] == "sid-2"
    assert raw_session.calls[0]["timeout"] == 11.0
    assert raw_session.calls[1]["timeout"] == 11.0


def test_add_torrent_uses_metainfo_for_bytes_and_returns_mapping_like_result() -> None:
    transport, raw_session = _transport_with_raw_session(
        [
            _Response(
                status_code=200,
                json_data={
                    "result": "success",
                    "arguments": {"torrent-added": {"id": 101, "name": "demo"}},
                },
            )
        ]
    )

    result = transport.add_torrent(b"abc", download_dir="/downloads", paused=True)

    payload = raw_session.calls[0]["kwargs"]["json"]
    assert payload["method"] == "torrent-add"
    assert payload["arguments"]["metainfo"] == base64.b64encode(b"abc").decode("ascii")
    assert payload["arguments"]["download-dir"] == "/downloads"
    assert payload["arguments"]["paused"] is True
    assert result["id"] == 101
    assert result.id == 101


def test_change_torrent_and_set_session_map_expected_rpc_argument_names() -> None:
    transport, raw_session = _transport_with_raw_session(
        [
            _Response(status_code=200, json_data={"result": "success", "arguments": {}}),
            _Response(status_code=200, json_data={"result": "success", "arguments": {}}),
        ]
    )

    transport.change_torrent(
        [1, 2],
        upload_limit=100,
        download_limit=200,
        upload_limited=True,
        download_limited=False,
        files_wanted=[0],
        priority_low=[2],
        tracker_add=["http://tracker.local/announce"],
    )
    transport.set_session(
        speed_limit_down=900,
        speed_limit_down_enabled=True,
        speed_limit_up=0,
        speed_limit_up_enabled=False,
    )

    first = raw_session.calls[0]["kwargs"]["json"]
    assert first["method"] == "torrent-set"
    assert first["arguments"] == {
        "ids": [1, 2],
        "uploadLimit": 100,
        "downloadLimit": 200,
        "uploadLimited": True,
        "downloadLimited": False,
        "files-wanted": [0],
        "priority-low": [2],
        "trackerAdd": ["http://tracker.local/announce"],
    }

    second = raw_session.calls[1]["kwargs"]["json"]
    assert second["method"] == "session-set"
    assert second["arguments"] == {
        "speed-limit-down": 900,
        "speed-limit-down-enabled": True,
        "speed-limit-up": 0,
        "speed-limit-up-enabled": False,
    }


def test_remove_move_and_rename_helpers_use_expected_rpc_shapes() -> None:
    transport, raw_session = _transport_with_raw_session(
        [
            _Response(status_code=200, json_data={"result": "success", "arguments": {}}),
            _Response(status_code=200, json_data={"result": "success", "arguments": {}}),
            _Response(
                status_code=200,
                json_data={"result": "success", "arguments": {"id": 7, "path": "new-name"}},
            ),
        ]
    )

    transport.remove_torrent([7, 8], delete_data=True)
    transport.move_torrent_data(7, location="/new/location", move=False, timeout=25)
    renamed = transport.rename_torrent_path(7, "old-name", "new-name")

    remove_payload = raw_session.calls[0]["kwargs"]["json"]
    assert remove_payload["method"] == "torrent-remove"
    assert remove_payload["arguments"] == {"ids": [7, 8], "delete-local-data": True}

    move_payload = raw_session.calls[1]["kwargs"]["json"]
    assert move_payload["method"] == "torrent-set-location"
    assert move_payload["arguments"] == {"ids": [7], "location": "/new/location", "move": False}
    assert raw_session.calls[1]["timeout"] == 25

    rename_payload = raw_session.calls[2]["kwargs"]["json"]
    assert rename_payload["method"] == "torrent-rename-path"
    assert rename_payload["arguments"] == {"ids": 7, "path": "old-name", "name": "new-name"}
    assert renamed.id == 7


@pytest.mark.parametrize(
    ("method_name", "rpc_method"),
    [
        ("start_torrent", "torrent-start"),
        ("stop_torrent", "torrent-stop"),
        ("verify_torrent", "torrent-verify"),
        ("reannounce_torrent", "torrent-reannounce"),
        ("queue_up", "queue-move-up"),
        ("queue_down", "queue-move-down"),
        ("queue_top", "queue-move-top"),
        ("queue_bottom", "queue-move-bottom"),
    ],
)
def test_action_helpers_call_expected_rpc_methods(method_name: str, rpc_method: str) -> None:
    transport, raw_session = _transport_with_raw_session(
        [_Response(status_code=200, json_data={"result": "success", "arguments": {}})]
    )

    getattr(transport, method_name)([3, 4])

    payload = raw_session.calls[0]["kwargs"]["json"]
    assert payload["method"] == rpc_method
    assert payload["arguments"] == {"ids": [3, 4]}


def test_get_session_normalizes_dash_keys_for_mapping_access() -> None:
    transport, _ = _transport_with_raw_session(
        [
            _Response(
                status_code=200,
                json_data={
                    "result": "success",
                    "arguments": {
                        "speed-limit-down": 1000,
                        "speed-limit-down-enabled": True,
                    },
                },
            )
        ]
    )

    session = transport.get_session()

    assert session.get("speed_limit_down") == 1000
    assert session.speed_limit_down_enabled is True


def test_get_torrent_uses_detail_fields_by_default_and_session_stats_returns_mapping() -> None:
    transport, raw_session = _transport_with_raw_session(
        [
            _Response(
                status_code=200,
                json_data={
                    "result": "success",
                    "arguments": {"torrents": [{"id": 3, "name": "x"}]},
                },
            ),
            _Response(
                status_code=200,
                json_data={"result": "success", "arguments": {"downloadSpeed": 11}},
            ),
        ]
    )

    torrent = transport.get_torrent(3)
    stats = transport.session_stats()

    get_payload = raw_session.calls[0]["kwargs"]["json"]
    assert get_payload["method"] == "torrent-get"
    assert get_payload["arguments"]["ids"] == [3]
    assert "files" in get_payload["arguments"]["fields"]
    assert "trackerStats" in get_payload["arguments"]["fields"]
    assert torrent is not None
    assert torrent.id == 3
    assert stats["downloadSpeed"] == 11


def test_transport_raises_authentication_error_and_protocol_error() -> None:
    auth_transport, _ = _transport_with_raw_session([_Response(status_code=401)])
    with pytest.raises(TransportAuthenticationError):
        auth_transport.get_torrents(arguments=["id"])

    protocol_transport, _ = _transport_with_raw_session(
        [_Response(status_code=200, json_data={"result": "duplicate torrent", "arguments": {}})]
    )
    with pytest.raises(TransportProtocolError, match="duplicate torrent"):
        protocol_transport.get_torrents(arguments=["id"])
