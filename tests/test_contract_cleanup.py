from __future__ import annotations

import pytest

from torrent_clients.client.base_client import (
    SupportsCategoryManagement,
    SupportsLazyTorrentFetch,
    TorrentQuery,
    UnsupportedClientCapabilityError,
)
from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient


class _QbTransportStub:
    def __init__(self) -> None:
        self.info_calls: list[dict[str, object]] = []
        self.files_calls: list[str] = []
        self.trackers_calls: list[str] = []
        self.properties_calls: list[str] = []

    def auth_log_in(self) -> bool:
        return True

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.info_calls.append(kwargs)
        return [
            {
                "hash": "hash-a",
                "name": "Demo",
                "save_path": "/downloads",
                "progress": 0.5,
                "total_size": 100,
                "size": 100,
                "completed": 50,
                "tags": "b, a, a",
                "state": "downloading",
                "added_on": 123,
                "dlspeed": 12,
                "upspeed": 6,
                "uploaded": 30,
                "num_leechs": 2,
                "num_seeds": 4,
                "category": "movies",
            }
        ]

    def torrents_files(self, *, torrent_hash):  # type: ignore[no-untyped-def]
        self.files_calls.append(torrent_hash)
        return [{"name": "movie.mkv", "size": 100, "progress": 0.5, "priority": 1}]

    def torrents_trackers(self, *, torrent_hash=None):  # type: ignore[no-untyped-def]
        self.trackers_calls.append(str(torrent_hash))
        return [
            {
                "url": "http://tracker.local/announce",
                "num_downloaded": 1,
                "num_seeds": 2,
                "num_leeches": 3,
                "num_peers": 4,
                "msg": "",
            }
        ]

    def torrents_properties(self, *, torrent_hash=None):  # type: ignore[no-untyped-def]
        self.properties_calls.append(str(torrent_hash))
        return {"comment": "detail comment"}


class _TransmissionTransportStub:
    def __init__(self) -> None:
        self.get_torrents_calls: list[dict[str, object]] = []
        self.get_torrent_calls: list[dict[str, object]] = []

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {
                "ids": tuple(ids) if ids is not None else None,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        return [
            {
                "id": 7,
                "hashString": "hash-tr",
                "name": "Transmission Demo",
                "downloadDir": "/downloads",
                "percentDone": 0.5,
                "totalSize": 100,
                "sizeWhenDone": 100,
                "haveValid": 50,
                "labels": ["tv", "tv", "archive"],
                "status": 4,
                "addedDate": 321,
            }
        ]

    def get_torrent(self, torrent_id, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrent_calls.append(
            {
                "torrent_id": torrent_id,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        return {
            "id": torrent_id,
            "hashString": "hash-tr",
            "name": "Transmission Demo",
            "downloadDir": "/downloads",
            "percentDone": 0.5,
            "totalSize": 100,
            "sizeWhenDone": 100,
            "haveValid": 50,
            "labels": ["tv", "archive"],
            "status": 4,
            "addedDate": 321,
            "comment": "detail comment",
            "files": [{"name": "episode.mkv", "length": 100, "bytesCompleted": 50}],
            "fileStats": [{"priority": 0, "wanted": True}],
            "trackerStats": [{"announce": "http://tracker.local/announce", "id": 9}],
            "__fields__": {
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
                "trackerStats",
            },
        }


def test_qb_summary_list_leaves_heavy_fields_unset() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    client.client = _QbTransportStub()

    torrent = client.get_torrents()[0]

    assert torrent.hash_string == "hash-a"
    assert torrent.labels == ["a", "b"]
    assert torrent.files is None
    assert torrent.trackers is None
    assert torrent.comment is None


def test_qb_detail_fetch_loads_files_trackers_and_comment_explicitly() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    stub = _QbTransportStub()
    client.client = stub

    torrent = client.get_torrent_info("hash-a")

    assert torrent is not None
    assert [(file.name, file.priority, file.wanted) for file in torrent.files] == [
        ("movie.mkv", 1, True)
    ]
    assert torrent.trackers[0] is not None
    assert torrent.trackers[0].url == "http://tracker.local/announce"
    assert torrent.comment == "detail comment"
    assert stub.files_calls == ["hash-a"]
    assert stub.trackers_calls == ["hash-a"]
    assert stub.properties_calls == ["hash-a"]


def test_transmission_summary_list_warns_for_deprecated_fields_and_keeps_heavy_fields_unset() -> (
    None
):
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")
    stub = _TransmissionTransportStub()
    client.client = stub

    with pytest.warns(DeprecationWarning, match="TorrentQuery.fields"):
        torrent = client.get_torrents(query=TorrentQuery(torrent_ids=[7], fields=["id", "files"]))[
            0
        ]

    assert stub.get_torrents_calls == [
        {
            "ids": (7,),
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
                "rateDownload",
                "rateUpload",
                "uploadedEver",
                "peersSendingToUs",
                "peersGettingFromUs",
            ),
        }
    ]
    assert torrent.files is None
    assert torrent.trackers is None
    assert torrent.comment is None


def test_transmission_detail_fetch_requests_explicit_detail_fields() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")
    stub = _TransmissionTransportStub()
    client.client = stub

    torrent = client.get_torrent_info(7)

    assert torrent is not None
    assert torrent.comment == "detail comment"
    assert [(file.name, file.priority, file.wanted) for file in torrent.files] == [
        ("episode.mkv", 0, True)
    ]
    assert torrent.trackers[0] is not None
    assert torrent.trackers[0].url == "http://tracker.local/announce"
    assert stub.get_torrent_calls == [
        {
            "torrent_id": 7,
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
                "rateDownload",
                "rateUpload",
                "uploadedEver",
                "peersSendingToUs",
                "peersGettingFromUs",
                "comment",
                "files",
                "fileStats",
                "trackerStats",
            ),
        }
    ]


def test_qb_get_torrents_original_emits_deprecation_warning() -> None:
    client = QbittorrentClient("http://localhost:8080/", "", "", name="qb")
    client.client = _QbTransportStub()

    with pytest.warns(DeprecationWarning, match="get_torrents_original"):
        client.get_torrents_original()


def test_transmission_get_torrents_lazy_emits_deprecation_warning() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")
    client.client = _TransmissionTransportStub()

    with pytest.warns(DeprecationWarning, match="get_torrents_lazy"):
        client.get_torrents_lazy()


def test_lazy_fetch_capability_checks_emit_deprecation_warning() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")

    with pytest.warns(DeprecationWarning, match="SupportsLazyTorrentFetch"):
        assert client.supports_capability(SupportsLazyTorrentFetch)

    with pytest.warns(DeprecationWarning, match="SupportsLazyTorrentFetch"):
        assert client.require_capability(SupportsLazyTorrentFetch) is client


def test_transmission_category_management_is_not_part_of_common_capabilities() -> None:
    client = TransmissionClient("http://localhost:9091/", "", "", name="tr")

    assert not client.supports_capability(SupportsCategoryManagement)
    with pytest.raises(UnsupportedClientCapabilityError, match="SupportsCategoryManagement"):
        client.require_capability(SupportsCategoryManagement)
