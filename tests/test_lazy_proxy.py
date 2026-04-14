from __future__ import annotations

from torrent_clients.client.qbittorrent_client import QbTorrentList
from torrent_clients.torrent.torrent_info import LazyProxy


def test_lazy_proxy_loads_on_first_access_and_caches() -> None:
    calls = {"count": 0}

    def loader() -> list[int]:
        calls["count"] += 1
        return [1, 2, 3]

    proxy = LazyProxy(loader)

    assert calls["count"] == 0
    assert repr(proxy) == "<LazyProxy (unloaded)>"

    assert proxy[0] == 1
    assert calls["count"] == 1

    assert len(proxy) == 3
    assert calls["count"] == 1


def test_qbtorrent_list_preserves_lazy_fields_until_access() -> None:
    class FakeTorrentData(dict):
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

    torrent_data = FakeTorrentData()
    torrent_info = QbTorrentList(raw=[torrent_data])[0]

    assert torrent_data.files_calls == 0
    assert torrent_data.trackers_calls == 0
    assert torrent_data.properties_calls == 0

    assert torrent_info.files[0].name == "a.bin"
    assert torrent_data.files_calls == 1

    assert torrent_info.trackers[0].url.startswith("http://")
    assert torrent_data.trackers_calls == 1

    assert str(torrent_info.comment) == "hello"
    assert torrent_data.properties_calls == 1
