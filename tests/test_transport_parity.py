from __future__ import annotations

from torrent_clients.client.qbittorrent_client import QbTorrentList
from torrent_clients.client.transmission_client import TrTorrentList
from torrent_clients.torrent.torrent_info import TorrentInfo


class _QbSdkTorrent(dict):
    def __init__(self, *, include_detail: bool = False) -> None:
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
        if include_detail:
            self["files"] = [
                {"name": "movie.mkv", "size": 123, "progress": 0.5, "priority": 1},
            ]
            self["trackers"] = [
                {
                    "url": "http://tracker.local/announce",
                    "num_downloaded": 1,
                    "num_seeds": 2,
                    "num_leeches": 3,
                    "num_peers": 4,
                    "msg": "",
                }
            ]
            self["comment"] = "hello"
        self.hash = "hash-a"
        self.name = "Movie"


class _TransmissionSdkTorrent:
    def __init__(self, *, include_detail: bool = False) -> None:
        self._data = {
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
        self.fields = set(self._data)
        if include_detail:
            self._data.update(
                {
                    "comment": "detail comment",
                    "files": [{"name": "episode.mkv", "length": 100, "bytesCompleted": 50}],
                    "fileStats": [{"priority": 0, "wanted": True}],
                    "trackerStats": [{"announce": "http://tracker.local/announce", "id": 9}],
                }
            )
            self.fields.update({"comment", "files", "fileStats", "trackerStats"})

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(key, default)


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


def test_qb_summary_normalization_matches_sdk_like_and_mapping_payloads() -> None:
    sdk_torrent = _QbSdkTorrent()
    mapping_torrent = {"hash": "hash-a", "name": "Movie", **dict(sdk_torrent)}

    sdk_result = QbTorrentList(raw=[sdk_torrent])[0]
    mapping_result = QbTorrentList(raw=[mapping_torrent])[0]

    assert _torrent_signature(sdk_result) == _torrent_signature(mapping_result)


def test_qb_detail_normalization_matches_sdk_like_and_mapping_payloads() -> None:
    sdk_torrent = _QbSdkTorrent(include_detail=True)
    mapping_torrent = {"hash": "hash-a", "name": "Movie", **dict(sdk_torrent)}

    sdk_result = QbTorrentList(raw=[sdk_torrent])[0]
    mapping_result = QbTorrentList(raw=[mapping_torrent])[0]

    assert _torrent_signature(sdk_result) == _torrent_signature(mapping_result)


def test_transmission_summary_normalization_matches_sdk_like_and_mapping_payloads() -> None:
    sdk_torrent = _TransmissionSdkTorrent()
    mapping_torrent = {**sdk_torrent._data, "__fields__": set(sdk_torrent.fields)}

    sdk_result = TrTorrentList(raw=[sdk_torrent])[0]
    mapping_result = TrTorrentList(raw=[mapping_torrent])[0]

    assert _torrent_signature(sdk_result) == _torrent_signature(mapping_result)


def test_transmission_detail_normalization_matches_sdk_like_and_mapping_payloads() -> None:
    sdk_torrent = _TransmissionSdkTorrent(include_detail=True)
    mapping_torrent = {**sdk_torrent._data, "__fields__": set(sdk_torrent.fields)}

    sdk_result = TrTorrentList(raw=[sdk_torrent])[0]
    mapping_result = TrTorrentList(raw=[mapping_torrent])[0]

    assert _torrent_signature(sdk_result) == _torrent_signature(mapping_result)
