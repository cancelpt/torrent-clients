from __future__ import annotations

from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient


def _new_qb_client() -> QbittorrentClient:
    return QbittorrentClient(
        "http://127.0.0.1:8080/",
        "",
        "",
        name="qb",
    )


def _new_tr_client() -> TransmissionClient:
    return TransmissionClient(
        "http://127.0.0.1:9091/",
        "",
        "",
        name="tr",
    )


class _TransmissionRPCStub:
    def __init__(self, response=None) -> None:
        self.get_torrents_calls = []
        self.response = response or []

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {
                "ids": tuple(ids) if ids is not None else None,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        return self.response


class _FakeTransmissionTorrent:
    def __init__(self, data: dict, fields: set[str]) -> None:
        self._data = data
        self.fields = fields

    @property
    def tracker_stats(self):  # type: ignore[no-untyped-def]
        return self._data.get("trackerStats", [])

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(key, default)


def test_qb_hydrate_files_uses_torrent_id_query() -> None:
    client = _new_qb_client()
    captured_queries = []

    def _fake_get_torrents(status=None, query=None):  # type: ignore[no-untyped-def]
        captured_queries.append((status, query))
        return ["hydrated"]

    client.get_torrents = _fake_get_torrents  # type: ignore[method-assign]

    result = client.hydrate_files(["hash-a", "hash-b"])

    assert result == ["hydrated"]
    assert captured_queries[0][0] is None
    assert captured_queries[0][1].torrent_ids == ["hash-a", "hash-b"]


def test_transmission_hydrate_files_requests_file_arguments() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.hydrate_files([1, 2])

    assert stub.get_torrents_calls == [
        {
            "ids": (1, 2),
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
                "files",
                "fileStats",
            ),
        }
    ]


def test_transmission_hydrate_files_returns_file_metadata_needed_by_audit() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub(
        response=[
            _FakeTransmissionTorrent(
                data={
                    "id": 1,
                    "hashString": "hash-a",
                    "name": "Movie",
                    "downloadDir": "/downloads",
                    "percentDone": 1.0,
                    "totalSize": 123,
                    "sizeWhenDone": 123,
                    "haveValid": 123,
                    "labels": ["done"],
                    "status": 6,
                    "addedDate": 100,
                    "files": [{"name": "movie.mkv", "length": 123, "bytesCompleted": 123}],
                    "fileStats": [{"priority": 0, "wanted": True}],
                },
                fields={
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
                    "files",
                    "fileStats",
                },
            )
        ]
    )
    client.client = stub

    result = client.hydrate_files([1])
    torrent = result[0]

    assert torrent.hash_string == "hash-a"
    assert torrent.name == "Movie"
    assert torrent.download_dir == "/downloads"
    assert torrent.progress == 1.0
    assert torrent.selected_size == 123
    assert torrent.completed_size == 123
    assert torrent.labels == ["done"]
    assert [(file.name, file.size) for file in torrent.files] == [("movie.mkv", 123)]


def test_qb_hydrate_trackers_uses_torrent_id_query() -> None:
    client = _new_qb_client()
    captured_queries = []

    def _fake_get_torrents(status=None, query=None):  # type: ignore[no-untyped-def]
        captured_queries.append((status, query))
        return ["hydrated"]

    client.get_torrents = _fake_get_torrents  # type: ignore[method-assign]

    result = client.hydrate_trackers(["hash-a", "hash-b"])

    assert result == ["hydrated"]
    assert captured_queries[0][0] is None
    assert captured_queries[0][1].torrent_ids == ["hash-a", "hash-b"]


def test_transmission_hydrate_trackers_requests_tracker_fields() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub(
        response=[
            _FakeTransmissionTorrent(
                data={
                    "id": 1,
                    "hashString": "hash-a",
                    "name": "Movie",
                    "downloadDir": "/downloads",
                    "trackerStats": [{"announce": "https://tracker.example/announce"}],
                },
                fields={"id", "hashString", "name", "downloadDir", "trackerStats"},
            )
        ]
    )
    client.client = stub

    result = client.hydrate_trackers([1])
    torrent = result[0]

    assert stub.get_torrents_calls == [
        {
            "ids": (1,),
            "arguments": ("id", "hashString", "name", "downloadDir", "trackerStats"),
        }
    ]
    assert [tracker.url for tracker in torrent.trackers] == [
        "https://tracker.example/announce"
    ]
