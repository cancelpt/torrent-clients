from __future__ import annotations

from types import SimpleNamespace

from torrent_clients.client.qbittorrent_client import QbittorrentClient
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
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
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


def _make_client(
    responses: list[_Response],
) -> tuple[QbittorrentClient, _RecordingRequestsSession]:
    raw_session = _RecordingRequestsSession(responses)
    session = HttpSession("http://qb.local:8080/", timeout=11)
    session._session = raw_session
    client = QbittorrentClient("http://qb.local:8080/", "test_user", "test_password", name="qb")
    client.client = QbittorrentTransport(
        "http://qb.local:8080/",
        "test_user",
        "test_password",
        session=session,
    )
    return client, raw_session


def test_get_torrent_info_filters_on_wire_with_official_hashes_param() -> None:
    client, raw_session = _make_client(
        [
            _Response(text="Ok."),
            _Response(
                json_data=[
                    {
                        "hash": "hash-a",
                        "name": "Demo",
                        "save_path": "/downloads",
                        "progress": 0.5,
                        "total_size": 100,
                        "size": 100,
                        "completed": 50,
                        "tags": "",
                        "state": "downloading",
                        "added_on": 1,
                        "dlspeed": 0,
                        "upspeed": 0,
                        "uploaded": 0,
                        "num_leechs": 0,
                        "num_seeds": 0,
                        "category": "",
                    }
                ]
            ),
            _Response(json_data=[]),
            _Response(json_data=[]),
            _Response(json_data={"comment": ""}),
        ]
    )

    info = client.get_torrent_info("hash-a")

    assert info is not None
    assert raw_session.calls[1]["kwargs"]["params"] == {"hashes": "hash-a"}


def test_hydrate_files_filters_on_wire_with_official_hashes_param() -> None:
    client, raw_session = _make_client(
        [
            _Response(text="Ok."),
            _Response(
                json_data=[
                    {
                        "hash": "hash-a",
                        "name": "A",
                        "save_path": "/downloads",
                        "progress": 1.0,
                        "total_size": 10,
                        "size": 10,
                        "completed": 10,
                        "tags": "",
                        "state": "pausedUP",
                        "added_on": 1,
                        "dlspeed": 0,
                        "upspeed": 0,
                        "uploaded": 0,
                        "num_leechs": 0,
                        "num_seeds": 0,
                        "category": "",
                    },
                    {
                        "hash": "hash-b",
                        "name": "B",
                        "save_path": "/downloads",
                        "progress": 1.0,
                        "total_size": 20,
                        "size": 20,
                        "completed": 20,
                        "tags": "",
                        "state": "pausedUP",
                        "added_on": 2,
                        "dlspeed": 0,
                        "upspeed": 0,
                        "uploaded": 0,
                        "num_leechs": 0,
                        "num_seeds": 0,
                        "category": "",
                    },
                ]
            ),
            _Response(json_data=[{"name": "a.bin", "size": 10, "progress": 1.0, "priority": 1}]),
            _Response(json_data=[{"name": "b.bin", "size": 20, "progress": 1.0, "priority": 1}]),
        ]
    )

    result = client.hydrate_files(["hash-a", "hash-b"])

    assert len(result) == 2
    assert raw_session.calls[1]["kwargs"]["params"] == {"hashes": "hash-a|hash-b"}


def test_hydrate_trackers_filters_on_wire_with_official_hashes_param() -> None:
    client, raw_session = _make_client(
        [
            _Response(text="Ok."),
            _Response(
                json_data=[
                    {
                        "hash": "hash-a",
                        "name": "A",
                        "save_path": "/downloads",
                        "progress": 1.0,
                        "total_size": 10,
                        "size": 10,
                        "completed": 10,
                        "tags": "",
                        "state": "pausedUP",
                        "added_on": 1,
                        "dlspeed": 0,
                        "upspeed": 0,
                        "uploaded": 0,
                        "num_leechs": 0,
                        "num_seeds": 0,
                        "category": "",
                    },
                    {
                        "hash": "hash-b",
                        "name": "B",
                        "save_path": "/downloads",
                        "progress": 1.0,
                        "total_size": 20,
                        "size": 20,
                        "completed": 20,
                        "tags": "",
                        "state": "pausedUP",
                        "added_on": 2,
                        "dlspeed": 0,
                        "upspeed": 0,
                        "uploaded": 0,
                        "num_leechs": 0,
                        "num_seeds": 0,
                        "category": "",
                    },
                ]
            ),
            _Response(
                json_data=[
                    {
                        "url": "https://tracker.example/a",
                        "num_downloaded": 0,
                        "num_seeds": 0,
                        "num_leeches": 0,
                        "num_peers": 0,
                        "msg": "",
                    }
                ]
            ),
            _Response(
                json_data=[
                    {
                        "url": "https://tracker.example/b",
                        "num_downloaded": 0,
                        "num_seeds": 0,
                        "num_leeches": 0,
                        "num_peers": 0,
                        "msg": "",
                    }
                ]
            ),
        ]
    )

    result = client.hydrate_trackers(["hash-a", "hash-b"])

    assert len(result) == 2
    assert raw_session.calls[1]["kwargs"]["params"] == {"hashes": "hash-a|hash-b"}


def test_add_torrent_uses_modern_stopped_payload_on_wire_for_qb_5_1() -> None:
    client, raw_session = _make_client(
        [
            _Response(text="Ok."),
            _Response(text="2.11.4"),
            _Response(text="Ok."),
        ]
    )

    ok = client.add_torrent(
        "magnet:?xt=urn:btih:abcdef",
        upload_limit=100,
        download_limit=200,
        download_dir="/downloads",
        is_paused=True,
        forced=True,
    )

    assert ok is True
    assert raw_session.calls[2]["kwargs"]["data"] == {
        "urls": "magnet:?xt=urn:btih:abcdef",
        "savepath": "/downloads",
        "stopped": "true",
        "forced": "true",
        "upLimit": 100,
        "dlLimit": 200,
        "skip_checking": "false",
        "category": "",
    }


def test_set_labels_many_uses_atomic_set_tags_on_wire_for_qb_5_1() -> None:
    client, raw_session = _make_client(
        [
            _Response(text="Ok."),
            _Response(text="2.11.4"),
            _Response(text=""),
        ]
    )

    client.set_labels_many(
        [
            (
                SimpleNamespace(id="hash-a", hash_string="hash-a", labels=["old-a"]),
                ["beta", "alpha"],
            ),
            (
                SimpleNamespace(id="hash-b", hash_string="hash-b", labels=["old-b"]),
                ["alpha", "beta"],
            ),
        ]
    )

    assert raw_session.calls[2]["url"] == "http://qb.local:8080/api/v2/torrents/setTags"
    assert raw_session.calls[2]["kwargs"]["data"] == {
        "hashes": "hash-a|hash-b",
        "tags": "alpha,beta",
    }
