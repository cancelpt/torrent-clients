from __future__ import annotations

from types import SimpleNamespace

import pytest

from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient
from torrent_clients.client_helper import fetch_torrent_snapshots


class _Downloader:
    def __init__(self, name: str, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled
        self.url = "http://localhost:1234/"
        self.username = ""
        self.password = ""
        self.dl_type = "qb"


class _SnapshotClient:
    def __init__(self, snapshots):
        self._snapshots = snapshots
        self.name = "stub"

    def get_torrents_snapshot(self):
        return self._snapshots


def test_fetch_torrent_snapshots_collects_partial_failures() -> None:
    downloaders = [_Downloader("ok"), _Downloader("bad")]

    def _factory(url, username, password, dl_type, name):
        _ = url, username, password, dl_type
        if name == "bad":
            raise RuntimeError("connect failed")
        return _SnapshotClient([SimpleNamespace(hash_string="hash-1")])

    result = fetch_torrent_snapshots(downloaders, client_factory=_factory)

    assert [snapshot.hash_string for snapshot in result.snapshots] == ["hash-1"]
    assert result.snapshot_id_to_client["hash-1"].name == "stub"
    assert result.failed_downloaders == ["bad"]


class _QbRPCStub:
    def __init__(self) -> None:
        self.last_info_kwargs = None

    def auth_log_in(self):  # type: ignore[no-untyped-def]
        return True

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_info_kwargs = kwargs
        return [
            _FakeQbSnapshot(
                hash_value="hash-qb",
                name="Demo",
                save_path="/downloads",
                progress=1.0,
                total_size=100,
                size=100,
                completed=100,
                tags="  B, A, ,A ",
                state="pausedUP",
                added_on=123,
            )
        ]


class _FakeQbSnapshot:
    def __init__(
        self,
        *,
        hash_value: str,
        name: str,
        save_path: str,
        progress: float,
        total_size: int,
        size: int,
        completed: int,
        tags: str,
        state: str,
        added_on: int,
    ) -> None:
        self.hash = hash_value
        self.name = name
        self._values = {
            "save_path": save_path,
            "progress": progress,
            "total_size": total_size,
            "size": size,
            "completed": completed,
            "tags": tags,
            "state": state,
            "added_on": added_on,
        }

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        return self._values.get(key, default)


def test_qb_client_get_torrents_snapshot_requests_lightweight_fields() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    stub = _QbRPCStub()
    client.client = stub

    client.get_torrents_snapshot()

    assert stub.last_info_kwargs["fields"] == [
        "hash",
        "name",
        "save_path",
        "progress",
        "total_size",
        "size",
        "completed",
        "tags",
        "state",
        "added_on",
    ]


def test_qb_client_get_torrents_snapshot_normalizes_status_and_labels() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    stub = _QbRPCStub()
    client.client = stub

    snapshots = client.get_torrents_snapshot()

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.status == "stopped"
    assert snapshot.labels == ["A", "B"]


def test_qb_client_get_torrents_snapshot_raises_for_missing_required_hash() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    stub = _QbRPCStub()

    class _MissingHashSnapshot(_FakeQbSnapshot):
        def __init__(self) -> None:
            super().__init__(
                hash_value="hash-qb",
                name="Demo",
                save_path="/downloads",
                progress=1.0,
                total_size=100,
                size=100,
                completed=100,
                tags="",
                state="pausedUP",
                added_on=123,
            )
            del self.hash

    def _broken_torrents_info(**kwargs):  # type: ignore[no-untyped-def]
        stub.last_info_kwargs = kwargs
        return [_MissingHashSnapshot()]

    stub.torrents_info = _broken_torrents_info  # type: ignore[method-assign]
    client.client = stub

    with pytest.raises(RuntimeError, match="missing required field.*hash"):
        client.get_torrents_snapshot()


class _TransmissionRPCStub:
    def __init__(self) -> None:
        self.get_torrents_calls = []

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {
                "ids": tuple(ids) if ids is not None else None,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        return [
            {
                "id": 9,
                "hashString": "hash-tr",
                "name": "Demo",
                "downloadDir": "/downloads",
                "percentDone": 0.5,
                "totalSize": 100,
                "sizeWhenDone": 100,
                "haveValid": 50,
                "labels": ["B", "A", "A", ""],
                "status": 6,
                "addedDate": 321,
            }
        ]


def test_transmission_client_get_torrents_snapshot_requests_lightweight_fields() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")
    stub = _TransmissionRPCStub()
    client.client = stub

    client.get_torrents_snapshot()

    assert stub.get_torrents_calls == [
        {
            "ids": None,
            "arguments": (
                "id",
                "hashString",
                "name",
                "downloadDir",
                "percentDone",
                "totalSize",
                "sizeWhenDone",
                "haveValid",
                "labels",
                "status",
                "addedDate",
            ),
        }
    ]


def test_transmission_client_get_torrents_snapshot_normalizes_status_and_labels() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")
    stub = _TransmissionRPCStub()
    client.client = stub

    snapshots = client.get_torrents_snapshot()

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.status == "seeding"
    assert snapshot.labels == ["A", "B"]
