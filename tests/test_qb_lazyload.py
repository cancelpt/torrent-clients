from __future__ import annotations

import time

from torrent_clients.client.qbittorrent_client import (
    QbTorrentFileList,
    QbTorrentList,
    QbTorrentTrackerList,
)
from torrent_clients.torrent.torrent_file import TorrentFile, TorrentFileList
from torrent_clients.torrent.torrent_info import TorrentInfo
from torrent_clients.torrent.torrent_status import DownloaderKind, convert_status


class SlowFakeTorrentData(dict):
    def __init__(self, delay_seconds: float = 0.0) -> None:
        super().__init__(
            save_path="/tmp",
            total_size=10,
            progress=0.5,
            dlspeed=0,
            upspeed=0,
            completed=5,
            size=10,
            category="",
            uploaded=0,
            num_leechs=0,
            num_seeds=0,
            added_on=0,
            tags="",
            state="downloading",
        )
        self.delay_seconds = delay_seconds
        self.files_calls = 0
        self.trackers_calls = 0
        self.properties_calls = 0
        self.hash = "abc123"
        self.name = "demo"

    @property
    def files(self):  # type: ignore[no-untyped-def]
        self.files_calls += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return [{"name": "a.bin", "size": 10, "progress": 0.5}]

    @property
    def trackers(self):  # type: ignore[no-untyped-def]
        self.trackers_calls += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return [
            {
                "url": "http://tracker.local/announce",
                "num_downloaded": 0,
                "num_seeds": 0,
                "num_leeches": 0,
                "num_peers": 0,
                "msg": "",
            }
        ]

    @property
    def properties(self):  # type: ignore[no-untyped-def]
        self.properties_calls += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return {"comment": "hello"}


def _non_lazy_transform(torrent_data: SlowFakeTorrentData) -> TorrentInfo:
    torrent_hash = torrent_data.hash
    tags = torrent_data.get("tags", "")
    labels = [label.strip() for label in tags.split(",")] if tags else []

    return TorrentInfo(
        id=torrent_hash,
        name=torrent_data.name,
        hash_string=torrent_hash,
        download_dir=torrent_data.get("save_path"),
        size=torrent_data.get("total_size", 0),
        progress=torrent_data.get("progress", 0),
        status=convert_status(
            torrent_data.get("state", "unknown"),
            DownloaderKind.QBITTORRENT,
        ),
        download_speed=torrent_data.get("dlspeed", 0),
        upload_speed=torrent_data.get("upspeed", 0),
        labels=labels,
        files=QbTorrentFileList(torrent_hash, raw=[torrent_data.files]),
        trackers=QbTorrentTrackerList(raw=torrent_data.trackers),
        completed_size=torrent_data.get("completed", 0),
        selected_size=torrent_data.get("size", 0),
        category=torrent_data.get("category", ""),
        uploaded_size=torrent_data.get("uploaded", 0),
        num_leechs=torrent_data.get("num_leechs", -1),
        num_seeds=torrent_data.get("num_seeds", -1),
        added_on=torrent_data.get("added_on", -1),
        comment=torrent_data.properties.get("comment", ""),
    )


def test_qb_lazyload_only_loads_trackers_when_trackers_are_accessed() -> None:
    torrent_data = SlowFakeTorrentData()
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_data.files_calls == 0
    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0

    assert len(torrent_info.trackers) == 1
    assert torrent_data.trackers_calls == 1
    assert torrent_data.files_calls == 0
    assert torrent_data.properties_calls == 0


def test_qb_lazyload_only_loads_files_when_file_list_is_accessed() -> None:
    torrent_data = SlowFakeTorrentData()
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_data.files_calls == 0
    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0

    assert len(torrent_info.files) == 1
    assert torrent_data.files_calls == 1
    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0


def test_qb_lazyload_is_faster_than_non_lazy_when_only_file_list_is_needed() -> None:
    rounds = 8
    delay_seconds = 0.01

    lazy_start = time.perf_counter()
    for _ in range(rounds):
        torrent_data = SlowFakeTorrentData(delay_seconds=delay_seconds)
        torrent_info = QbTorrentList(raw=[torrent_data])[0]
        _ = len(torrent_info.files)
    lazy_elapsed = time.perf_counter() - lazy_start

    non_lazy_start = time.perf_counter()
    for _ in range(rounds):
        torrent_data = SlowFakeTorrentData(delay_seconds=delay_seconds)
        torrent_info = _non_lazy_transform(torrent_data)
        _ = len(torrent_info.files)
    non_lazy_elapsed = time.perf_counter() - non_lazy_start

    assert lazy_elapsed < non_lazy_elapsed * 0.7, (
        f"lazy={lazy_elapsed:.4f}s, non_lazy={non_lazy_elapsed:.4f}s"
    )


def test_torrent_file_list_reuses_transformed_entries_across_access_patterns() -> None:
    class _CountingTorrentFileList(TorrentFileList):
        def __init__(self) -> None:
            super().__init__("demo", raw=[[{"name": "a.bin", "size": 10}, {"name": "b.bin", "size": 20}]])
            self.transform_calls = 0

        def transform(self, file_data):  # type: ignore[no-untyped-def]
            self.transform_calls += 1
            file_item = file_data[0]
            return TorrentFile(name=file_item["name"], size=file_item["size"])

    file_list = _CountingTorrentFileList()

    assert [item.name for item in file_list] == ["a.bin", "b.bin"]
    assert [item.name for item in file_list.details] == ["a.bin", "b.bin"]
    assert file_list[0].name == "a.bin"
    assert file_list.transform_calls == 2


def test_torrent_file_path_preserves_leading_and_trailing_spaces() -> None:
    file_entry = TorrentFile(name="  folder\\a.bin  ", size=10)

    assert file_entry.path == "  folder/a.bin  "


def test_torrent_file_list_iter_path_names_uses_compat_wrapper() -> None:
    class _CountingTorrentFileList(TorrentFileList):
        def __init__(self) -> None:
            super().__init__(
                "demo",
                raw=[[{"name": "  folder\\a.bin  ", "size": 10}, {"name": "b.bin", "size": 20}]],
            )
            self.transform_calls = 0

        def transform(self, file_data):  # type: ignore[no-untyped-def]
            self.transform_calls += 1
            file_item = file_data[0]
            return TorrentFile(name=file_item["name"], size=file_item["size"])

    file_list = _CountingTorrentFileList()

    assert list(file_list.iter_path_names()) == [
        ("  folder/a.bin  ", "  folder\\a.bin  "),
        ("b.bin", "b.bin"),
    ]
    assert file_list.transform_calls == 2


def test_qb_torrent_file_list_iter_path_names_is_adapter_opt_in_and_preserves_spaces() -> None:
    class _CountingQbTorrentFileList(QbTorrentFileList):
        def __init__(self) -> None:
            super().__init__(
                "demo",
                raw=[[{"name": "  folder\\a.bin  ", "size": 10}, {"name": "b.bin", "size": 20}]],
            )
            self.transform_calls = 0

        def transform(self, file_data):  # type: ignore[no-untyped-def]
            self.transform_calls += 1
            return super().transform(file_data)

    file_list = _CountingQbTorrentFileList()

    assert list(file_list.iter_path_names()) == [
        ("  folder/a.bin  ", "  folder\\a.bin  "),
        ("b.bin", "b.bin"),
    ]
    assert file_list.transform_calls == 0


def test_qb_torrent_file_list_iter_file_entries_is_adapter_opt_in_and_preserves_sizes() -> None:
    class _CountingQbTorrentFileList(QbTorrentFileList):
        def __init__(self) -> None:
            super().__init__(
                "demo",
                raw=[[{"name": "  folder\\a.bin  ", "size": 10}, {"name": "b.bin", "size": 20}]],
            )
            self.transform_calls = 0

        def transform(self, file_data):  # type: ignore[no-untyped-def]
            self.transform_calls += 1
            return super().transform(file_data)

    file_list = _CountingQbTorrentFileList()

    assert list(file_list.iter_file_entries()) == [
        {"path": "  folder/a.bin  ", "origin": "  folder\\a.bin  ", "size": 10},
        {"path": "b.bin", "origin": "b.bin", "size": 20},
    ]
    assert file_list.transform_calls == 0
