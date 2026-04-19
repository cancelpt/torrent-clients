from __future__ import annotations

from torrent_clients.client.qbittorrent_client import (
    QbTorrentFileList,
    QbTorrentList,
)
from torrent_clients.torrent.torrent_file import TorrentFile, TorrentFileList


class SlowFakeTorrentData(dict):
    def __init__(self) -> None:
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
        self.files_calls = 0
        self.trackers_calls = 0
        self.properties_calls = 0
        self.hash = "abc123"
        self.name = "demo"

    @property
    def files(self):  # type: ignore[no-untyped-def]
        self.files_calls += 1
        return [{"name": "a.bin", "size": 10, "progress": 0.5}]

    @property
    def trackers(self):  # type: ignore[no-untyped-def]
        self.trackers_calls += 1
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
        return {"comment": "hello"}


def test_qb_summary_transform_leaves_heavy_fields_unset() -> None:
    torrent_data = SlowFakeTorrentData()
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_data.files_calls == 0
    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0
    assert torrent_info.files is None
    assert torrent_info.trackers is None
    assert torrent_info.comment is None


def test_qb_transform_populates_files_when_transport_payload_includes_them() -> None:
    torrent_data = SlowFakeTorrentData()
    torrent_data["files"] = [{"name": "a.bin", "size": 10, "progress": 0.5, "priority": 1}]
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0
    assert [(file.name, file.priority, file.wanted) for file in torrent_info.files] == [
        ("a.bin", 1, True)
    ]


def test_qb_transform_populates_trackers_and_comment_when_transport_payload_includes_them() -> None:
    torrent_data = SlowFakeTorrentData()
    torrent_data["trackers"] = [
        {
            "url": "http://tracker.local/announce",
            "num_downloaded": 0,
            "num_seeds": 0,
            "num_leeches": 0,
            "num_peers": 0,
            "msg": "",
        }
    ]
    torrent_data["comment"] = "hello"
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_info.trackers[0] is not None
    assert torrent_info.trackers[0].url == "http://tracker.local/announce"
    assert torrent_info.comment == "hello"


def test_torrent_file_list_reuses_transformed_entries_across_access_patterns() -> None:
    class _CountingTorrentFileList(TorrentFileList):
        def __init__(self) -> None:
            super().__init__(
                "demo", raw=[[{"name": "a.bin", "size": 10}, {"name": "b.bin", "size": 20}]]
            )
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


def test_qb_torrent_file_list_preserves_non_zero_priority_as_wanted() -> None:
    file_list = QbTorrentFileList(
        "demo",
        raw=[[{"name": "movie.mkv", "size": 123, "progress": 0.5, "priority": 7}]],
    )

    file_entry = file_list[0]

    assert file_entry.priority == 7
    assert file_entry.wanted is True
