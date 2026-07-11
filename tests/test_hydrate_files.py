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
        return self._data["trackerStats"]

    @property
    def comment(self) -> str:
        return self._data["comment"]

    @property
    def labels(self) -> list[str]:
        return self._data["labels"]

    @property
    def status(self):  # type: ignore[no-untyped-def]
        return self._data["status"]

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(key, default)


class _FilteringTransmissionRPCStub(_TransmissionRPCStub):
    def __init__(self, torrents: list[dict]) -> None:
        super().__init__()
        self._torrents = torrents

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {
                "ids": tuple(ids) if ids is not None else None,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        requested_fields = set(arguments or [])
        return [
            _FakeTransmissionTorrent(
                data={key: value for key, value in torrent.items() if key in requested_fields},
                fields={key for key in requested_fields if key in torrent},
            )
            for torrent in self._torrents
        ]


class _QbRPCStub:
    def __init__(self, response=None) -> None:
        self.last_info_kwargs = None
        self.response = response or []

    def auth_log_in(self) -> bool:
        return True

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_info_kwargs = kwargs
        return self.response


class _FakeQbTorrent(dict):
    def __init__(self, files: list[dict] | None = None) -> None:
        super().__init__(
            save_path="/downloads",
            total_size=123,
            progress=1.0,
            dlspeed=0,
            upspeed=0,
            completed=123,
            size=123,
            category="",
            uploaded=0,
            num_leechs=0,
            num_seeds=0,
            added_on=100,
            tags="done",
            state="pausedUP",
        )
        self.hash = "hash-a"
        self.name = "Movie"
        self._files = files or []

    @property
    def files(self):  # type: ignore[no-untyped-def]
        return self._files

    @property
    def trackers(self):  # type: ignore[no-untyped-def]
        return []

    @property
    def properties(self):  # type: ignore[no-untyped-def]
        return {"comment": ""}


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


def test_qb_hydrate_files_returns_file_selection_metadata() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub(
        response=[
            _FakeQbTorrent(
                files=[
                    {"name": "movie.mkv", "size": 123, "progress": 1.0, "priority": 0},
                    {"name": "sample.mkv", "size": 23, "progress": 1.0, "priority": 6},
                ]
            )
        ]
    )
    client.client = stub

    result = client.hydrate_files(["hash-a"])
    torrent = result[0]

    assert stub.last_info_kwargs == {"status_filter": None, "torrent_hashes": ["hash-a"]}
    assert [(file.name, file.priority, file.wanted) for file in torrent.files] == [
        ("movie.mkv", 0, False),
        ("sample.mkv", 6, True),
    ]


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
                "comment",
                "files",
                "fileStats",
            ),
        }
    ]


def test_transmission_hydrate_files_returns_file_metadata_needed_by_audit() -> None:
    client = _new_tr_client()
    stub = _FilteringTransmissionRPCStub(
        torrents=[
            {
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
                "comment": "Release notes",
                "files": [{"name": "movie.mkv", "length": 123, "bytesCompleted": 123}],
                "fileStats": [{"priority": 0, "wanted": True}],
            }
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
    assert torrent.comment == "Release notes"
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
    stub = _FilteringTransmissionRPCStub(
        torrents=[
            {
                "id": 1,
                "hashString": "hash-a",
                "name": "Movie",
                "downloadDir": "/downloads",
                "labels": ["movies"],
                "status": 4,
                "comment": "Tracker metadata",
                "trackerStats": [{"announce": "https://tracker.example/announce"}],
            }
        ]
    )
    client.client = stub

    result = client.hydrate_trackers([1])
    torrent = result[0]

    assert stub.get_torrents_calls == [
        {
            "ids": (1,),
            "arguments": (
                "id",
                "hashString",
                "name",
                "downloadDir",
                "labels",
                "status",
                "comment",
                "trackerStats",
            ),
        }
    ]
    assert torrent.labels == ["movies"]
    assert torrent.comment == "Tracker metadata"
    assert [tracker.url for tracker in torrent.trackers] == ["https://tracker.example/announce"]
