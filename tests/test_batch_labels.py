from __future__ import annotations

from types import SimpleNamespace

from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient


class _QbRPCStub:
    def __init__(self) -> None:
        self.add_calls = []
        self.remove_calls = []

    def torrents_add_tags(self, torrent_hashes=None, tags=None):  # type: ignore[no-untyped-def]
        self.add_calls.append((torrent_hashes, tags))

    def torrents_remove_tags(self, torrent_hashes=None, tags=None):  # type: ignore[no-untyped-def]
        self.remove_calls.append((torrent_hashes, tags))


class _TransmissionRPCStub:
    def __init__(self) -> None:
        self.change_calls = []

    def change_torrent(self, torrent_id, labels=None, **kwargs):  # type: ignore[no-untyped-def]
        self.change_calls.append(
            {
                "torrent_id": torrent_id,
                "labels": labels,
                "kwargs": kwargs,
            }
        )


def test_transmission_set_labels_many_groups_updates_by_target_labels() -> None:
    client = TransmissionClient("http://127.0.0.1:9091/", "", "", name="tr")
    stub = _TransmissionRPCStub()
    client.client = stub
    torrent_a = SimpleNamespace(id=1, labels=["old-a"])
    torrent_b = SimpleNamespace(id=2, labels=["old-b"])
    torrent_c = SimpleNamespace(id=3, labels=["old-c"])

    client.set_labels_many(
        [
            (torrent_a, ["A", "B"]),
            (torrent_b, ["B", "A"]),
            (torrent_c, ["C"]),
        ]
    )

    assert stub.change_calls == [
        {"torrent_id": [1, 2], "labels": ["A", "B"], "kwargs": {}},
        {"torrent_id": [3], "labels": ["C"], "kwargs": {}},
    ]


def test_qb_set_labels_many_groups_add_and_remove_deltas() -> None:
    client = QbittorrentClient("http://127.0.0.1:8080/", "", "", name="qb")
    stub = _QbRPCStub()
    client.client = stub
    torrent_a = SimpleNamespace(id="hash-a", hash_string="hash-a", labels=["A"])
    torrent_b = SimpleNamespace(id="hash-b", hash_string="hash-b", labels=["C"])
    torrent_c = SimpleNamespace(
        id="hash-c",
        hash_string="hash-c",
        labels=["D", "X"],
    )

    client.set_labels_many(
        [
            (torrent_a, ["A", "B"]),
            (torrent_b, ["C", "B"]),
            (torrent_c, ["D"]),
        ]
    )

    assert stub.add_calls == [(["hash-a", "hash-b"], ["B"])]
    assert stub.remove_calls == [(["hash-c"], ["X"])]
