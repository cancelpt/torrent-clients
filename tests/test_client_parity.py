from __future__ import annotations

from torrent_clients.client.base_client import TorrentSnapshot
from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient
from torrent_clients.torrent.torrent_info import TorrentInfo


class _QbSdkTorrent(dict):
    def __init__(self) -> None:
        super().__init__(
            save_path="/downloads",
            total_size=123,
            progress=0.5,
            dlspeed=12,
            upspeed=6,
            completed=61,
            size=123,
            category="movies",
            uploaded=30,
            num_leechs=2,
            num_seeds=4,
            added_on=100,
            tags="b, a, a",
            state="downloading",
        )
        self.hash = "hash-a"
        self.name = "Movie"


class _QbClientStub:
    def auth_log_in(self) -> bool:
        return True

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        return [self._torrent_payload()]

    def torrents_files(self, *, torrent_hash):  # type: ignore[no-untyped-def]
        assert torrent_hash == "hash-a"
        return [{"name": "movie.mkv", "size": 123, "progress": 0.5, "priority": 1}]

    def torrents_trackers(self, *, torrent_hash):  # type: ignore[no-untyped-def]
        assert torrent_hash == "hash-a"
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

    def torrents_properties(self, *, torrent_hash):  # type: ignore[no-untyped-def]
        assert torrent_hash == "hash-a"
        return {"comment": "detail comment"}


class _QbSdkClientStub(_QbClientStub):
    @staticmethod
    def _torrent_payload() -> _QbSdkTorrent:
        return _QbSdkTorrent()


class _QbOwnedClientStub(_QbClientStub):
    @staticmethod
    def _torrent_payload() -> dict[str, object]:
        torrent = _QbSdkTorrent()
        return {"hash": "hash-a", "name": "Movie", **dict(torrent)}


class _TransmissionSdkTorrent:
    def __init__(self, data: dict[str, object], fields: set[str]) -> None:
        self._data = data
        self.fields = fields

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(key, default)


class _TransmissionClientStub:
    _summary = {
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
        "rateDownload": 12,
        "rateUpload": 6,
        "uploadedEver": 30,
        "peersSendingToUs": 2,
        "peersGettingFromUs": 4,
    }
    _detail = {
        **_summary,
        "comment": "detail comment",
        "files": [{"name": "episode.mkv", "length": 100, "bytesCompleted": 50}],
        "fileStats": [{"priority": 0, "wanted": True}],
        "trackerStats": [{"announce": "http://tracker.local/announce", "id": 9}],
    }

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        _ = ids
        requested = set(arguments or [])
        if {"files", "fileStats", "trackerStats", "comment"} & requested:
            return [self._make_torrent(self._detail, requested)]
        return [self._make_torrent(self._summary, requested or set(self._summary))]

    def get_torrent(self, torrent_id, arguments=None):  # type: ignore[no-untyped-def]
        assert torrent_id == 7
        return self._make_torrent(self._detail, set(arguments or self._detail))


class _TransmissionSdkClientStub(_TransmissionClientStub):
    @staticmethod
    def _make_torrent(data: dict[str, object], fields: set[str]):
        return _TransmissionSdkTorrent(data, fields)


class _TransmissionOwnedClientStub(_TransmissionClientStub):
    @staticmethod
    def _make_torrent(data: dict[str, object], fields: set[str]):
        payload = {key: value for key, value in data.items() if key in fields}
        payload["__fields__"] = set(fields)
        return payload


def _torrent_signature(torrent: TorrentInfo) -> dict[str, object]:
    files = None
    if torrent.files is not None:
        files = [
            (
                file.name,
                file.size,
                file.priority,
                file.wanted,
                file.completed_size,
            )
            for file in torrent.files
        ]

    trackers = None
    if torrent.trackers is not None:
        trackers = [
            (
                tracker.url,
                tracker.downloaded,
                tracker.seeder,
                tracker.leecher,
                tracker.peers,
                tracker.info,
            )
            for tracker in torrent.trackers
            if tracker is not None
        ]

    status = torrent.status.value if torrent.status is not None else None

    return {
        "id": torrent.id,
        "name": torrent.name,
        "hash_string": torrent.hash_string,
        "download_dir": torrent.download_dir,
        "size": torrent.size,
        "progress": torrent.progress,
        "status": status,
        "download_speed": torrent.download_speed,
        "upload_speed": torrent.upload_speed,
        "labels": torrent.labels,
        "files": files,
        "trackers": trackers,
        "completed_size": torrent.completed_size,
        "selected_size": torrent.selected_size,
        "category": torrent.category,
        "uploaded_size": torrent.uploaded_size,
        "num_leechs": torrent.num_leechs,
        "num_seeds": torrent.num_seeds,
        "added_on": torrent.added_on,
        "comment": torrent.comment,
    }


def _snapshot_signature(snapshot: TorrentSnapshot) -> dict[str, object]:
    return {
        "id": snapshot.id,
        "hash_string": snapshot.hash_string,
        "name": snapshot.name,
        "download_dir": snapshot.download_dir,
        "progress": snapshot.progress,
        "size": snapshot.size,
        "selected_size": snapshot.selected_size,
        "completed_size": snapshot.completed_size,
        "labels": snapshot.labels,
        "status": snapshot.status,
        "added_on": snapshot.added_on,
    }


def test_qb_client_fetch_flows_match_sdk_like_and_owned_mapping_stubs() -> None:
    sdk_client = QbittorrentClient("http://localhost:8080/", "", "", name="qb-sdk")
    owned_client = QbittorrentClient("http://localhost:8080/", "", "", name="qb-owned")
    sdk_client.client = _QbSdkClientStub()
    owned_client.client = _QbOwnedClientStub()

    assert [_torrent_signature(torrent) for torrent in sdk_client.get_torrents()] == [
        _torrent_signature(torrent) for torrent in owned_client.get_torrents()
    ]
    assert [_snapshot_signature(snapshot) for snapshot in sdk_client.get_torrents_snapshot()] == [
        _snapshot_signature(snapshot) for snapshot in owned_client.get_torrents_snapshot()
    ]
    assert _torrent_signature(sdk_client.get_torrent_info("hash-a")) == _torrent_signature(
        owned_client.get_torrent_info("hash-a")
    )
    assert [_torrent_signature(torrent) for torrent in sdk_client.hydrate_files(["hash-a"])] == [
        _torrent_signature(torrent) for torrent in owned_client.hydrate_files(["hash-a"])
    ]
    assert [_torrent_signature(torrent) for torrent in sdk_client.hydrate_trackers(["hash-a"])] == [
        _torrent_signature(torrent) for torrent in owned_client.hydrate_trackers(["hash-a"])
    ]


def test_transmission_client_fetch_flows_match_sdk_like_and_owned_mapping_stubs() -> None:
    sdk_client = TransmissionClient("http://localhost:9091/", "", "", name="tr-sdk")
    owned_client = TransmissionClient("http://localhost:9091/", "", "", name="tr-owned")
    sdk_client.client = _TransmissionSdkClientStub()
    owned_client.client = _TransmissionOwnedClientStub()

    assert [_torrent_signature(torrent) for torrent in sdk_client.get_torrents()] == [
        _torrent_signature(torrent) for torrent in owned_client.get_torrents()
    ]
    assert [_snapshot_signature(snapshot) for snapshot in sdk_client.get_torrents_snapshot()] == [
        _snapshot_signature(snapshot) for snapshot in owned_client.get_torrents_snapshot()
    ]
    assert _torrent_signature(sdk_client.get_torrent_info(7)) == _torrent_signature(
        owned_client.get_torrent_info(7)
    )
    assert [_torrent_signature(torrent) for torrent in sdk_client.hydrate_files([7])] == [
        _torrent_signature(torrent) for torrent in owned_client.hydrate_files([7])
    ]
    assert [_torrent_signature(torrent) for torrent in sdk_client.hydrate_trackers([7])] == [
        _torrent_signature(torrent) for torrent in owned_client.hydrate_trackers([7])
    ]
