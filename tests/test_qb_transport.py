from __future__ import annotations

import io
import json

import pytest

from torrent_clients.transport.errors import (
    TransportAuthenticationError,
    TransportProtocolError,
)
from torrent_clients.transport.http import HttpSession
from torrent_clients.transport.qbittorrent import QbittorrentTransport


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


def _make_transport(
    responses: list[_Response],
) -> tuple[QbittorrentTransport, _RecordingRequestsSession]:
    raw_session = _RecordingRequestsSession(responses=responses)
    session = HttpSession("http://qb.local:8080/", timeout=11)
    session._session = raw_session
    return (
        QbittorrentTransport(
            "http://qb.local:8080/", "test_user", "test_password", session=session
        ),
        raw_session,
    )


def test_auth_login_once_and_session_reuse() -> None:
    transport, raw_session = _make_transport(
        [
            _Response(text="Ok."),
            _Response(json_data=[{"hash": "hash-a"}]),
            _Response(json_data=[{"hash": "hash-b"}]),
        ]
    )

    first = transport.torrents_info()
    second = transport.torrents_info()

    assert first == [{"hash": "hash-a"}]
    assert second == [{"hash": "hash-b"}]
    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/torrents/info",
        "http://qb.local:8080/api/v2/torrents/info",
    ]
    assert raw_session.calls[0]["timeout"] == 11
    assert raw_session.calls[1]["timeout"] == 11


def test_auth_failure_raises_repository_auth_error() -> None:
    transport, _ = _make_transport([_Response(text="Fails.")])

    with pytest.raises(TransportAuthenticationError):
        transport.auth_log_in()


def test_legacy_resume_pause_for_pre_5_web_api() -> None:
    transport, raw_session = _make_transport(
        [
            _Response(text="Ok."),
            _Response(text="2.10.4"),
            _Response(text="Ok."),
            _Response(text="Ok."),
        ]
    )

    transport.torrents_start(torrent_hashes=["hash-a"])
    transport.torrents_stop(torrent_hashes=["hash-a"])

    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/app/webapiVersion",
        "http://qb.local:8080/api/v2/torrents/resume",
        "http://qb.local:8080/api/v2/torrents/pause",
    ]


def test_start_stop_for_5x_web_api() -> None:
    transport, raw_session = _make_transport(
        [
            _Response(text="Ok."),
            _Response(text="2.11.0"),
            _Response(text="Ok."),
            _Response(text="Ok."),
        ]
    )

    transport.torrents_start(torrent_hashes=["hash-a"])
    transport.torrents_stop(torrent_hashes=["hash-a"])

    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/app/webapiVersion",
        "http://qb.local:8080/api/v2/torrents/start",
        "http://qb.local:8080/api/v2/torrents/stop",
    ]


def test_torrents_add_supports_urls_and_file_payloads() -> None:
    transport, raw_session = _make_transport(
        [
            _Response(text="Ok."),
            _Response(text="Ok."),
            _Response(text="Ok."),
        ]
    )

    from_urls = transport.torrents_add(
        urls=["magnet:?xt=urn:btih:abc", "http://example.com/demo.torrent"]
    )
    from_files = transport.torrents_add(
        torrent_files={"demo.torrent": io.BytesIO(b"torrent-binary")},
        save_path="/downloads",
        is_paused=True,
    )

    assert from_urls["ok"] is True
    assert from_files["ok"] is True
    add_urls_data = raw_session.calls[1]["kwargs"]["data"]
    add_files_data = raw_session.calls[2]["kwargs"]["data"]
    add_files = raw_session.calls[2]["kwargs"]["files"]
    assert add_urls_data["urls"] == "magnet:?xt=urn:btih:abc\nhttp://example.com/demo.torrent"
    assert add_files_data["savepath"] == "/downloads"
    assert add_files_data["paused"] == "true"
    assert add_files[0][0] == "torrents"
    assert add_files[0][1][0] == "demo.torrent"


def test_core_methods_use_expected_endpoints_and_mapping_payloads() -> None:
    transport, raw_session = _make_transport(
        [
            _Response(text="Ok."),
            _Response(json_data=[{"name": "f1.mkv"}]),
            _Response(json_data=[{"url": "http://tracker.local/announce"}]),
            _Response(json_data={"comment": "demo"}),
            _Response(json_data={"peers": {"p1": {"ip": "127.0.0.1"}}}),
            _Response(json_data={"dl_info_speed": 100, "up_info_speed": 50}),
            _Response(json_data={"banned_IPs": ""}),
            _Response(text="Ok."),
            _Response(text="Ok."),
            _Response(text="Ok."),
            _Response(text="Ok."),
            _Response(text="Ok."),
            _Response(text="Ok."),
        ]
    )

    files_payload = transport.torrents_files(torrent_hash="hash-a")
    trackers_payload = transport.torrents_trackers(torrent_hash="hash-a")
    properties_payload = transport.torrents_properties(torrent_hash="hash-a")
    peers_payload = transport.sync_torrent_peers(torrent_hash="hash-a")
    stats_payload = transport.transfer_info()
    prefs_payload = transport.app_preferences()
    set_prefs_result = transport.app_set_preferences(prefs={"banned_IPs": "1.1.1.1"})
    add_trackers_result = transport.torrents_add_trackers(
        torrent_hash="hash-a",
        urls=["http://tracker.one/announce", "udp://tracker.two:1337/announce"],
    )
    remove_trackers_result = transport.torrents_remove_trackers(
        torrent_hash="hash-a",
        urls=["http://tracker.one/announce", "udp://tracker.two:1337/announce"],
    )
    edit_tracker_result = transport.torrents_edit_tracker(
        torrent_hash="hash-a",
        original_url="http://tracker.one/announce",
        new_url="http://tracker.three/announce",
    )
    rename_result = transport.torrents_rename(torrent_hash="hash-a", new_torrent_name="new-name")
    rename_file_result = transport.torrents_rename_file(
        torrent_hash="hash-a",
        old_path="old.bin",
        new_path="new.bin",
    )

    assert files_payload == [{"name": "f1.mkv"}]
    assert trackers_payload == [{"url": "http://tracker.local/announce"}]
    assert properties_payload == {"comment": "demo"}
    assert peers_payload == {"peers": {"p1": {"ip": "127.0.0.1"}}}
    assert stats_payload == {"dl_info_speed": 100, "up_info_speed": 50}
    assert prefs_payload == {"banned_IPs": ""}
    assert set_prefs_result["ok"] is True
    assert add_trackers_result["ok"] is True
    assert remove_trackers_result["ok"] is True
    assert edit_tracker_result["ok"] is True
    assert rename_result["ok"] is True
    assert rename_file_result["ok"] is True

    assert [call["url"] for call in raw_session.calls] == [
        "http://qb.local:8080/api/v2/auth/login",
        "http://qb.local:8080/api/v2/torrents/files",
        "http://qb.local:8080/api/v2/torrents/trackers",
        "http://qb.local:8080/api/v2/torrents/properties",
        "http://qb.local:8080/api/v2/sync/torrentPeers",
        "http://qb.local:8080/api/v2/transfer/info",
        "http://qb.local:8080/api/v2/app/preferences",
        "http://qb.local:8080/api/v2/app/setPreferences",
        "http://qb.local:8080/api/v2/torrents/addTrackers",
        "http://qb.local:8080/api/v2/torrents/removeTrackers",
        "http://qb.local:8080/api/v2/torrents/editTracker",
        "http://qb.local:8080/api/v2/torrents/rename",
        "http://qb.local:8080/api/v2/torrents/renameFile",
    ]
    set_prefs_data = raw_session.calls[7]["kwargs"]["data"]
    add_trackers_data = raw_session.calls[8]["kwargs"]["data"]
    remove_trackers_data = raw_session.calls[9]["kwargs"]["data"]
    edit_trackers_data = raw_session.calls[10]["kwargs"]["data"]
    assert json.loads(set_prefs_data["json"]) == {"banned_IPs": "1.1.1.1"}
    assert add_trackers_data["urls"] == (
        "http://tracker.one/announce\nudp://tracker.two:1337/announce"
    )
    assert remove_trackers_data["urls"] == (
        "http://tracker.one/announce|udp://tracker.two:1337/announce"
    )
    assert edit_trackers_data == {
        "hash": "hash-a",
        "origUrl": "http://tracker.one/announce",
        "newUrl": "http://tracker.three/announce",
    }


def test_invalid_json_raises_protocol_error() -> None:
    transport, _ = _make_transport([_Response(text="Ok."), _Response(text="not-json")])

    with pytest.raises(TransportProtocolError):
        transport.torrents_info()
