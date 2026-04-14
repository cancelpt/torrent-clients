from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from torrent_clients.client.transmission_client import (
    MissingTorrentFieldError,
    TrTorrentFileList,
    TransmissionClient,
)


@dataclass
class _FakeTorrent:
    data: dict
    fields: set[str]

    @property
    def tracker_stats(self):  # type: ignore[no-untyped-def]
        return self.data.get("trackerStats", [])

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self.data.get(key, default)


class _FakeTransmissionRPCClient:
    def __init__(self, total: int = 3) -> None:
        self._dataset = []
        for torrent_id in range(1, total + 1):
            self._dataset.append(
                {
                    "id": torrent_id,
                    "totalSize": torrent_id * 100,
                    "name": f"torrent-{torrent_id}",
                    "status": "downloading",
                    "files": [{"name": f"f-{torrent_id}.bin", "length": torrent_id}],
                    "fileStats": [
                        {
                            "priority": 0,
                            "wanted": True,
                        }
                    ],
                    "trackerStats": [
                        {
                            "announce": f"http://tracker-{torrent_id}.local/announce",
                            "downloaded": 0,
                            "seeder_count": 0,
                            "leecher_count": 0,
                            "last_announce_result": "",
                        }
                    ],
                }
            )
        self.get_torrents_calls: list[dict] = []
        self.get_torrent_calls: list[tuple[int, tuple[str, ...] | None]] = []

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {"ids": tuple(ids) if ids is not None else None, "arguments": tuple(arguments or [])}
        )

        requested = list(arguments or ["id", "totalSize", "name", "status"])
        if "id" not in requested:
            requested.append("id")

        selected = self._dataset
        if ids is not None:
            selected_ids = set(int(torrent_id) for torrent_id in ids)
            selected = [row for row in self._dataset if row["id"] in selected_ids]

        result = []
        for row in selected:
            data = {key: row[key] for key in requested if key in row}
            result.append(_FakeTorrent(data=data, fields=set(requested)))
        return result

    def get_torrent(self, torrent_id, arguments=None):  # type: ignore[no-untyped-def]
        args_tuple = tuple(arguments) if arguments is not None else None
        self.get_torrent_calls.append((torrent_id, args_tuple))
        requested = list(arguments or ["id", "totalSize", "name", "status"])
        if "id" not in requested:
            requested.append("id")

        row = next(item for item in self._dataset if item["id"] == torrent_id)
        data = {key: row[key] for key in requested if key in row}
        return _FakeTorrent(data=data, fields=set(requested))

    def detail_calls(self) -> list[dict]:
        return [call for call in self.get_torrents_calls if call["ids"] is not None]


def _build_client(fake_rpc: _FakeTransmissionRPCClient) -> TransmissionClient:
    client = TransmissionClient(
        os.getenv("TEST_TR_URL", "http://localhost:9091/"),
        os.getenv("TEST_TR_USERNAME", ""),
        os.getenv("TEST_TR_PASSWORD", ""),
        name="tr",
    )
    client.client = fake_rpc
    return client


def test_default_mode_prefetches_scalar_size_without_extra_fetch() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(arguments=None)

    assert torrents[0].size == 100
    assert len(fake_rpc.detail_calls()) == 0


def test_default_mode_lazily_fetches_files() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(
        arguments=None,
        batch_size=10,
        promote_thresholds={"files": 99},
    )

    assert len(fake_rpc.detail_calls()) == 0
    assert len(torrents[0].files) == 1
    assert len(fake_rpc.detail_calls()) == 1


def test_default_mode_promotes_to_batched_fetch_after_threshold() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(
        arguments=None,
        batch_size=10,
        promote_thresholds={"files": 2},
    )

    assert len(torrents[0].files) == 1
    assert fake_rpc.detail_calls()[0]["ids"] == (1,)

    assert len(torrents[1].files) == 1
    assert fake_rpc.detail_calls()[1]["ids"] == (2, 3)

    assert len(torrents[2].files) == 1
    assert len(fake_rpc.detail_calls()) == 2


def test_user_arguments_enable_strict_mode_and_missing_scalar_field_raises() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(arguments=["id"])

    with pytest.raises(MissingTorrentFieldError, match="totalSize"):
        _ = torrents[0].size

    assert len(fake_rpc.detail_calls()) == 0
    assert len(fake_rpc.get_torrent_calls) == 0


def test_user_arguments_enable_strict_mode_and_missing_heavy_field_raises() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(arguments=["id", "totalSize"])

    with pytest.raises(MissingTorrentFieldError, match="files"):
        _ = torrents[0].files

    assert len(fake_rpc.detail_calls()) == 0
    assert len(fake_rpc.get_torrent_calls) == 0


def test_user_arguments_strict_mode_allows_only_requested_fields() -> None:
    fake_rpc = _FakeTransmissionRPCClient(total=3)
    client = _build_client(fake_rpc)

    torrents = client.get_torrents_lazy(arguments=["id", "totalSize", "name"])

    assert torrents[0].size == 100
    assert torrents[0].name == "torrent-1"
    assert len(fake_rpc.detail_calls()) == 0
    assert len(fake_rpc.get_torrent_calls) == 0


def test_tr_torrent_file_list_iter_path_names_is_adapter_opt_in_and_preserves_spaces() -> None:
    file_list = TrTorrentFileList(
        1,
        raw=[
            [{"name": "  folder\\episode.mkv  ", "length": 10}],
            [{"priority": 0, "wanted": True}],
        ],
    )

    assert list(file_list.iter_path_names()) == [
        ("  folder/episode.mkv  ", "  folder\\episode.mkv  ")
    ]


def test_tr_torrent_file_list_iter_file_entries_is_adapter_opt_in_and_preserves_sizes() -> None:
    file_list = TrTorrentFileList(
        1,
        raw=[
            [{"name": "  folder\\episode.mkv  ", "length": 10}],
            [{"priority": 0, "wanted": True}],
        ],
    )

    assert list(file_list.iter_file_entries()) == [
        {
            "path": "  folder/episode.mkv  ",
            "origin": "  folder\\episode.mkv  ",
            "size": 10,
        }
    ]
