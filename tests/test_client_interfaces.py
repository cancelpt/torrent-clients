from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from torrent_clients.client.base_client import (
    ClientStats,
    QueueDirection,
    SupportsIpBan,
    SupportsLazyTorrentFetch,
    TorrentQuery,
    UnsupportedClientCapabilityError,
)
from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import (
    TransmissionClient,
    TrTorrentList,
    TrTorrentTrackerList,
)
from torrent_clients.torrent.torrent_info import TorrentInfo
from torrent_clients.torrent.torrent_status import TorrentStatus


class _FakeTorrent:
    def __init__(self, data: dict, fields: set[str]) -> None:
        self._data = data
        self.fields = fields

    @property
    def tracker_stats(self):  # type: ignore[no-untyped-def]
        return self._data.get("trackerStats", [])

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(key, default)


class _TransmissionRPCStub:
    def __init__(self) -> None:
        self.added_payload = None
        self._upload_limit = -1
        self._download_limit = -1
        self.started = []
        self.removed_calls = []
        self.stopped_calls = []
        self.verified_calls = []
        self.get_torrents_calls = []
        self.change_calls = []
        self.reannounce_calls = []
        self.queue_calls = []
        self.rename_calls = []
        self.set_session_calls = []

    def add_torrent(  # type: ignore[no-untyped-def]
        self,
        torrent_input,
        download_dir=None,
        paused=True,
    ):
        _ = download_dir, paused
        self.added_payload = torrent_input
        return SimpleNamespace(id=101)

    def change_torrent(  # type: ignore[no-untyped-def]
        self,
        torrent_id,
        upload_limit=None,
        download_limit=None,
        upload_limited=True,
        download_limited=True,
        **kwargs,
    ):
        self.change_calls.append(
            {
                "torrent_id": torrent_id,
                "upload_limit": upload_limit,
                "download_limit": download_limit,
                "upload_limited": upload_limited,
                "download_limited": download_limited,
                "kwargs": kwargs,
            }
        )
        self._upload_limit = upload_limit
        self._download_limit = download_limit

    def get_torrent(self, torrent_id, arguments=None):  # type: ignore[no-untyped-def]
        if arguments and "peers" in arguments:
            return _FakeTorrent(
                data={
                    "id": torrent_id,
                    "peers": [
                        {
                            "clientName": "uTorrent",
                            "rateToClient": 12,
                            "rateToPeer": 3,
                            "downloadedEver": 100,
                            "uploadedEver": 90,
                            "address": "1.2.3.4",
                            "port": 51413,
                            "progress": 0.5,
                            "flagStr": "DU",
                        }
                    ],
                },
                fields={"id", "peers"},
            )
        if arguments and "trackerStats" in arguments:
            return _FakeTorrent(
                data={
                    "id": torrent_id,
                    "trackerStats": [
                        {"id": 10, "announce": "http://old.tracker/a"},
                        {"id": 11, "announce": "http://other.tracker/b"},
                    ],
                },
                fields={"id", "trackerStats"},
            )
        if arguments and "name" in arguments:
            return _FakeTorrent(data={"id": torrent_id, "name": "old-name"}, fields={"id", "name"})
        return SimpleNamespace(upload_limit=self._upload_limit, download_limit=self._download_limit)

    def get_torrents(self, ids=None, arguments=None):  # type: ignore[no-untyped-def]
        self.get_torrents_calls.append(
            {
                "ids": tuple(ids) if ids is not None else None,
                "arguments": tuple(arguments) if arguments is not None else None,
            }
        )
        return []

    def remove_torrent(self, ids, delete_data=False):  # type: ignore[no-untyped-def]
        self.removed_calls.append((ids, delete_data))

    def start_torrent(self, _torrent_id):  # type: ignore[no-untyped-def]
        self.started.append(_torrent_id)

    def stop_torrent(self, ids):  # type: ignore[no-untyped-def]
        self.stopped_calls.append(ids)

    def verify_torrent(self, ids):  # type: ignore[no-untyped-def]
        self.verified_calls.append(ids)

    def reannounce_torrent(self, ids):  # type: ignore[no-untyped-def]
        self.reannounce_calls.append(ids)

    def queue_up(self, ids):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("up", ids))

    def queue_down(self, ids):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("down", ids))

    def queue_top(self, ids):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("top", ids))

    def queue_bottom(self, ids):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("bottom", ids))

    def rename_torrent_path(self, torrent_id, location, name):  # type: ignore[no-untyped-def]
        self.rename_calls.append((torrent_id, location, name))
        return location, name

    def set_session(self, **kwargs):  # type: ignore[no-untyped-def]
        self.set_session_calls.append(kwargs)

    def session_stats(self):  # type: ignore[no-untyped-def]
        return {"downloadSpeed": 123, "uploadSpeed": 456}

    def get_session(self):  # type: ignore[no-untyped-def]
        return {
            "speed_limit_down_enabled": True,
            "speed_limit_down": 1000,
            "speed_limit_up_enabled": False,
            "speed_limit_up": 0,
        }


class _FakeQbTorrent(dict):
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
        self.hash = "abc123"
        self.name = "demo"

    @property
    def files(self):  # type: ignore[no-untyped-def]
        return [{"name": "a.bin", "size": 10, "progress": 0.5}]

    @property
    def trackers(self):  # type: ignore[no-untyped-def]
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
        return {"comment": "hello"}

    @property
    def state(self):  # type: ignore[no-untyped-def]
        return self.get("state")


class _QbRPCStub:
    def __init__(self) -> None:
        self.deleted_calls = []
        self.recheck_calls = []
        self.pause_calls = []
        self.resume_calls = []
        self.start_calls = []
        self.stop_calls = []
        self.last_info_kwargs = None
        self.reannounce_calls = []
        self.set_upload_limit_calls = []
        self.set_download_limit_calls = []
        self.queue_calls = []
        self.file_priority_calls = []
        self.add_trackers_calls = []
        self.remove_trackers_calls = []
        self.edit_trackers_calls = []
        self.rename_calls = []
        self.rename_file_calls = []
        self.transfer_upload_limit_calls = []
        self.transfer_download_limit_calls = []

    def auth_log_in(self) -> bool:
        return True

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_info_kwargs = kwargs
        return [_FakeQbTorrent()]

    def torrents_delete(self, torrent_hashes=None, delete_files=False):  # type: ignore[no-untyped-def]
        self.deleted_calls.append((torrent_hashes, delete_files))

    def torrents_recheck(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.recheck_calls.append(torrent_hashes)

    def torrents_pause(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.pause_calls.append(torrent_hashes)

    def torrents_resume(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.resume_calls.append(torrent_hashes)

    def torrents_start(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.start_calls.append(torrent_hashes)

    def torrents_stop(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.stop_calls.append(torrent_hashes)

    def torrents_reannounce(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.reannounce_calls.append(torrent_hashes)

    def torrents_set_upload_limit(self, limit=None, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.set_upload_limit_calls.append((limit, torrent_hashes))

    def torrents_set_download_limit(self, limit=None, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.set_download_limit_calls.append((limit, torrent_hashes))

    def torrents_top_priority(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("top", torrent_hashes))

    def torrents_bottom_priority(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("bottom", torrent_hashes))

    def torrents_increase_priority(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("up", torrent_hashes))

    def torrents_decrease_priority(self, torrent_hashes=None):  # type: ignore[no-untyped-def]
        self.queue_calls.append(("down", torrent_hashes))

    def torrents_file_priority(self, torrent_hash=None, file_ids=None, priority=None):  # type: ignore[no-untyped-def]
        self.file_priority_calls.append((torrent_hash, file_ids, priority))

    def torrents_trackers(self, torrent_hash=None):  # type: ignore[no-untyped-def]
        _ = torrent_hash
        return [
            {
                "url": "http://old.tracker/a",
                "num_downloaded": 0,
                "num_seeds": 0,
                "num_leeches": 0,
                "num_peers": 0,
                "msg": "",
            }
        ]

    def torrents_add_trackers(self, torrent_hash=None, urls=None):  # type: ignore[no-untyped-def]
        self.add_trackers_calls.append((torrent_hash, urls))

    def torrents_remove_trackers(self, torrent_hash=None, urls=None):  # type: ignore[no-untyped-def]
        self.remove_trackers_calls.append((torrent_hash, urls))

    def torrents_edit_tracker(  # type: ignore[no-untyped-def]
        self,
        torrent_hash=None,
        original_url=None,
        new_url=None,
        **kwargs,
    ):
        if original_url is None and "orig_url" in kwargs:
            original_url = kwargs["orig_url"]
        self.edit_trackers_calls.append((torrent_hash, original_url, new_url))

    def torrents_rename(self, torrent_hash=None, new_torrent_name=None):  # type: ignore[no-untyped-def]
        self.rename_calls.append((torrent_hash, new_torrent_name))

    def torrents_rename_file(self, torrent_hash=None, old_path=None, new_path=None):  # type: ignore[no-untyped-def]
        self.rename_file_calls.append((torrent_hash, old_path, new_path))

    def transfer_set_upload_limit(self, limit=None):  # type: ignore[no-untyped-def]
        self.transfer_upload_limit_calls.append(limit)

    def transfer_set_download_limit(self, limit=None):  # type: ignore[no-untyped-def]
        self.transfer_download_limit_calls.append(limit)

    def transfer_info(self):  # type: ignore[no-untyped-def]
        return {
            "dl_info_speed": 111,
            "up_info_speed": 222,
            "dl_rate_limit": 1000,
            "up_rate_limit": 2000,
        }


class _QbMoveRPCStub(_QbRPCStub):
    def __init__(self, moved_to: str) -> None:
        super().__init__()
        self.moved_to = moved_to
        self.set_location_calls = []

    def torrents_set_location(self, torrent_hashes=None, location=None):  # type: ignore[no-untyped-def]
        self.set_location_calls.append((torrent_hashes, location))

    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_info_kwargs = kwargs
        torrent = _FakeQbTorrent()
        torrent["save_path"] = self.moved_to
        torrent["progress"] = 1.0
        torrent["state"] = "pausedUP"
        return [torrent]


class _QbMoveReStopRPCStub(_QbMoveRPCStub):
    def torrents_info(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_info_kwargs = kwargs
        torrent = _FakeQbTorrent()
        torrent["save_path"] = self.moved_to
        torrent["progress"] = 0.5
        torrent["state"] = "stoppedDL"
        return [torrent]


def _new_qb_client() -> QbittorrentClient:
    return QbittorrentClient(
        os.getenv("TEST_QB_URL", "http://127.0.0.1:8080/"),
        os.getenv("TEST_QB_USERNAME", ""),
        os.getenv("TEST_QB_PASSWORD", ""),
        name="qb",
    )


def _new_tr_client() -> TransmissionClient:
    return TransmissionClient(
        os.getenv("TEST_TR_URL", "http://127.0.0.1:9091/"),
        os.getenv("TEST_TR_USERNAME", ""),
        os.getenv("TEST_TR_PASSWORD", ""),
        name="tr",
    )


def test_transmission_add_torrent_supports_magnet_input() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    ok = client.add_torrent("magnet:?xt=urn:btih:abcdef", upload_limit=100, download_limit=200)

    assert ok is True
    assert isinstance(stub.added_payload, str)
    assert stub.added_payload.startswith("magnet:")


def test_transmission_add_torrent_supports_local_file_input(tmp_path: Path) -> None:
    torrent_file = tmp_path / "sample.torrent"
    torrent_file.write_bytes(b"d8:announce13:http://a.ee")

    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    ok = client.add_torrent(str(torrent_file))

    assert ok is True
    assert isinstance(stub.added_payload, bytes)
    assert len(stub.added_payload) > 0


def test_transmission_get_peer_info_returns_mapped_peer_list() -> None:
    client = _new_tr_client()
    client.client = _TransmissionRPCStub()

    peers = client.get_peer_info(101)

    assert len(peers) == 1
    first = peers[0]
    assert first.client == "uTorrent"
    assert first.ip == "1.2.3.4"
    assert first.downloaded == 100
    assert first.uploaded == 90


def test_qb_get_torrent_info_returns_single_result() -> None:
    client = _new_qb_client()
    client.client = _QbRPCStub()

    info = client.get_torrent_info("abc123")

    assert info is not None
    assert info.id == "abc123"


def test_qb_supports_ip_ban_capability() -> None:
    client = _new_qb_client()

    assert client.supports_capability(SupportsIpBan)
    assert client.require_capability(SupportsIpBan) is client


def test_transmission_supports_lazy_fetch_capability() -> None:
    client = _new_tr_client()

    assert client.supports_capability(SupportsLazyTorrentFetch)
    assert client.require_capability(SupportsLazyTorrentFetch) is client


def test_transmission_require_ip_ban_capability_raises() -> None:
    client = _new_tr_client()

    assert not client.supports_capability(SupportsIpBan)
    with pytest.raises(UnsupportedClientCapabilityError):
        client.require_capability(SupportsIpBan)


def test_qb_remove_torrent_supports_batch_and_delete_data_flag() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.remove_torrent(["hash-a", "hash-b"], delete_data=True)

    assert stub.deleted_calls == [(["hash-a", "hash-b"], True)]


def test_transmission_remove_torrent_supports_batch_and_delete_data_flag() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.remove_torrent([1, 2, 3], delete_data=True)

    assert stub.removed_calls == [([1, 2, 3], True)]


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


def test_qb_lifecycle_methods_support_batch_ids() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.pause_torrent(["hash-a", "hash-b"])
    client.resume_torrent(["hash-a", "hash-b"])
    client.recheck_torrent(["hash-a", "hash-b"])

    assert stub.stop_calls == [["hash-a", "hash-b"]]
    assert stub.start_calls == [["hash-a", "hash-b"]]
    assert stub.pause_calls == []
    assert stub.resume_calls == []
    assert stub.recheck_calls == [["hash-a", "hash-b"]]


def test_transmission_lifecycle_methods_support_batch_ids() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.pause_torrent([1, 2])
    client.resume_torrent([1, 2])
    client.recheck_torrent([1, 2])

    assert stub.stopped_calls == [[1, 2]]
    assert stub.started == [[1, 2]]
    assert stub.verified_calls == [[1, 2]]


def test_qb_get_torrents_supports_query_object_filters() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    query = TorrentQuery(
        category="cat-a",
        tag="tag-a",
        sort="name",
        reverse=True,
        limit=20,
        offset=3,
        torrent_ids=["hash-a", "hash-b"],
    )
    _ = client.get_torrents(status="downloading", query=query)

    assert stub.last_info_kwargs == {
        "status_filter": "downloading",
        "category": "cat-a",
        "tag": "tag-a",
        "sort": "name",
        "reverse": True,
        "limit": 20,
        "offset": 3,
        "torrent_hashes": ["hash-a", "hash-b"],
    }


def test_transmission_get_torrents_supports_query_object_fields() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    query = TorrentQuery(
        torrent_ids=[101, 202],
        fields=["id", "name", "status"],
    )
    _ = client.get_torrents(query=query)

    assert stub.get_torrents_calls == [{"ids": (101, 202), "arguments": ("id", "name", "status")}]


def test_qb_move_torrent_returns_true_when_location_matches() -> None:
    client = _new_qb_client()
    stub = _QbMoveRPCStub(moved_to="/new/path")
    client.client = stub
    torrent = TorrentInfo(
        id="abc123",
        name="demo",
        hash_string="abc123",
        progress=1.0,
        status=TorrentStatus.STOPPED,
    )

    moved = client.move_torrent(torrent, "/new/path", move_files=True)

    assert moved is True


def test_qb_move_torrent_reapplies_stop_helper_when_completed_stopped_torrent_becomes_incomplete() -> (
    None
):
    client = _new_qb_client()
    stub = _QbMoveReStopRPCStub(moved_to="/new/path")
    client.client = stub
    torrent = TorrentInfo(
        id="abc123",
        name="demo",
        hash_string="abc123",
        progress=1.0,
        status=TorrentStatus.STOPPED,
    )

    moved = client.move_torrent(torrent, "/new/path", move_files=True)

    assert moved is True
    assert stub.stop_calls == ["abc123"]
    assert stub.pause_calls == []


def test_qb_reannounce_torrent_supports_batch_ids() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.reannounce_torrent(["hash-a", "hash-b"])

    assert stub.reannounce_calls == [["hash-a", "hash-b"]]


def test_transmission_reannounce_torrent_supports_batch_ids() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.reannounce_torrent([1, 2])

    assert stub.reannounce_calls == [[1, 2]]


def test_qb_set_torrent_limits_updates_upload_and_download_limit() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.set_torrent_limits(["hash-a", "hash-b"], download_limit=1024, upload_limit=2048)

    assert stub.set_download_limit_calls == [(1024, ["hash-a", "hash-b"])]
    assert stub.set_upload_limit_calls == [(2048, ["hash-a", "hash-b"])]


def test_transmission_set_torrent_limits_uses_change_torrent() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.set_torrent_limits([1, 2], download_limit=300, upload_limit=400)

    assert stub.change_calls[-1] == {
        "torrent_id": [1, 2],
        "upload_limit": 400,
        "download_limit": 300,
        "upload_limited": True,
        "download_limited": True,
        "kwargs": {},
    }


def test_qb_move_queue_maps_top_direction() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.move_queue(["hash-a", "hash-b"], QueueDirection.TOP)

    assert stub.queue_calls == [("top", ["hash-a", "hash-b"])]


def test_transmission_move_queue_maps_down_direction() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.move_queue([1, 2], QueueDirection.DOWN)

    assert stub.queue_calls == [("down", [1, 2])]


def test_qb_set_files_sets_not_wanted_as_priority_zero() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.set_files("abc123", [0, 3], wanted=False)

    assert stub.file_priority_calls == [("abc123", [0, 3], 0)]


def test_transmission_set_files_maps_wanted_and_priority() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.set_files(101, [1, 2], wanted=True, priority=-1)

    assert stub.change_calls[-1] == {
        "torrent_id": 101,
        "upload_limit": None,
        "download_limit": None,
        "upload_limited": True,
        "download_limited": True,
        "kwargs": {"files_wanted": [1, 2], "priority_low": [1, 2]},
    }


def test_qb_tracker_management_calls_native_apis() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    trackers = client.list_trackers("abc123")
    client.add_trackers("abc123", ["http://one/announce", "http://two/announce"])
    client.remove_trackers("abc123", ["http://one/announce"])
    client.replace_tracker("abc123", "http://old.tracker/a", "http://new.tracker/a")

    assert len(trackers) == 1
    assert trackers[0] is not None
    assert trackers[0].url == "http://old.tracker/a"
    assert stub.add_trackers_calls == [("abc123", ["http://one/announce", "http://two/announce"])]
    assert stub.remove_trackers_calls == [("abc123", ["http://one/announce"])]
    assert stub.edit_trackers_calls == [("abc123", "http://old.tracker/a", "http://new.tracker/a")]


def test_transmission_tracker_management_maps_urls_to_tracker_ids() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    trackers = client.list_trackers(101)
    client.add_trackers(101, ["http://new.tracker/a"])
    client.remove_trackers(101, ["http://old.tracker/a"])
    client.replace_tracker(101, "http://old.tracker/a", "http://replace.tracker/a")

    assert len(trackers) == 2
    assert trackers[0] is not None
    assert trackers[0].url == "http://old.tracker/a"
    assert stub.change_calls[-3:] == [
        {
            "torrent_id": 101,
            "upload_limit": None,
            "download_limit": None,
            "upload_limited": True,
            "download_limited": True,
            "kwargs": {"tracker_add": ["http://new.tracker/a"]},
        },
        {
            "torrent_id": 101,
            "upload_limit": None,
            "download_limit": None,
            "upload_limited": True,
            "download_limited": True,
            "kwargs": {"tracker_remove": [10]},
        },
        {
            "torrent_id": 101,
            "upload_limit": None,
            "download_limit": None,
            "upload_limited": True,
            "download_limited": True,
            "kwargs": {"tracker_replace": [(10, "http://replace.tracker/a")]},
        },
    ]


def test_qb_rename_torrent_supports_name_and_path_modes() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.rename_torrent("abc123", new_name="renamed-torrent")
    client.rename_torrent("abc123", old_path="old/path.mkv", new_path="new/path.mkv")

    assert stub.rename_calls == [("abc123", "renamed-torrent")]
    assert stub.rename_file_calls == [("abc123", "old/path.mkv", "new/path.mkv")]


def test_transmission_rename_torrent_uses_rename_path() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.rename_torrent(101, old_path="old-folder", new_path="new-folder")

    assert stub.rename_calls == [(101, "old-folder", "new-folder")]


def test_qb_set_global_limits_sets_both_directions() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    client.set_global_limits(download_limit=1200, upload_limit=3400)

    assert stub.transfer_download_limit_calls == [1200]
    assert stub.transfer_upload_limit_calls == [3400]


def test_transmission_set_global_limits_maps_to_session_fields() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    client.set_global_limits(download_limit=900, upload_limit=-1)

    assert stub.set_session_calls == [
        {
            "speed_limit_down": 900,
            "speed_limit_down_enabled": True,
            "speed_limit_up": 0,
            "speed_limit_up_enabled": False,
        }
    ]


def test_qb_get_client_stats_uses_transfer_info() -> None:
    client = _new_qb_client()
    stub = _QbRPCStub()
    client.client = stub

    stats = client.get_client_stats()

    expected = {
        "download_speed": 111,
        "upload_speed": 222,
        "download_limit": 1000,
        "upload_limit": 2000,
    }

    assert isinstance(stats, ClientStats)
    assert stats.download_speed == 111
    assert stats["upload_speed"] == 222
    assert dict(stats.items()) == expected
    assert stats == expected


def test_qb_get_client_stats_logs_missing_expected_fields(caplog: pytest.LogCaptureFixture) -> None:
    client = _new_qb_client()

    class _MissingFieldQbRPCStub(_QbRPCStub):
        def transfer_info(self):  # type: ignore[no-untyped-def]
            return {
                "up_info_speed": 222,
                "dl_rate_limit": 1000,
                "up_rate_limit": 2000,
            }

    client.client = _MissingFieldQbRPCStub()

    with caplog.at_level("WARNING", logger="torrent_clients.client.qbittorrent_client"):
        stats = client.get_client_stats()

    assert stats.download_speed == 0
    assert "dl_info_speed" in caplog.text


def test_qb_get_client_stats_does_not_log_for_legitimate_zero_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _new_qb_client()

    class _ZeroValueQbRPCStub(_QbRPCStub):
        def transfer_info(self):  # type: ignore[no-untyped-def]
            return {
                "dl_info_speed": 0,
                "up_info_speed": 0,
                "dl_rate_limit": 0,
                "up_rate_limit": 0,
            }

    client.client = _ZeroValueQbRPCStub()

    with caplog.at_level("WARNING", logger="torrent_clients.client.qbittorrent_client"):
        stats = client.get_client_stats()

    assert stats.download_speed == 0
    assert stats.upload_speed == 0
    assert "missing expected field" not in caplog.text


def test_transmission_get_client_stats_merges_session_and_stats() -> None:
    client = _new_tr_client()
    stub = _TransmissionRPCStub()
    client.client = stub

    stats = client.get_client_stats()

    expected = {
        "download_speed": 123,
        "upload_speed": 456,
        "download_limit": 1000,
        "upload_limit": 0,
        "download_limited": True,
        "upload_limited": False,
    }

    assert isinstance(stats, ClientStats)
    assert stats.download_limit == 1000
    assert stats.get("upload_limited") is False
    assert dict(stats.items()) == expected
    assert stats == expected


def test_transmission_get_client_stats_logs_missing_expected_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _new_tr_client()

    class _MissingFieldTransmissionRPCStub(_TransmissionRPCStub):
        def session_stats(self):  # type: ignore[no-untyped-def]
            return {"uploadSpeed": 456}

    client.client = _MissingFieldTransmissionRPCStub()

    with caplog.at_level("WARNING", logger="torrent_clients.client.transmission_client"):
        stats = client.get_client_stats()

    assert stats.download_speed == 0
    assert "downloadSpeed" in caplog.text


def test_transmission_torrent_transform_raises_for_missing_required_name() -> None:
    broken_torrent = _FakeTorrent(
        data={
            "id": 101,
            "hashString": "hash-101",
            "downloadDir": "/downloads",
        },
        fields={"id", "hashString", "downloadDir"},
    )

    with pytest.raises(RuntimeError, match="missing required field.*name"):
        _ = TrTorrentList(raw=[broken_torrent])[0]


def test_transmission_tracker_transform_keeps_missing_counts_as_sentinels() -> None:
    trackers = TrTorrentTrackerList(
        raw=[
            {
                "announce": "http://tracker.local/announce",
            }
        ]
    )

    tracker = trackers[0]

    assert tracker is not None
    assert tracker.downloaded == -1
    assert tracker.seeder == -1
    assert tracker.leecher == -1
    assert tracker.peers == -1


def test_client_stats_supports_attribute_and_mapping_access() -> None:
    stats = ClientStats(
        download_speed=1,
        upload_speed=2,
        download_limit=3,
        upload_limit=4,
    )

    assert stats.download_speed == 1
    assert stats["upload_speed"] == 2
    assert stats.get("missing", "fallback") == "fallback"
    assert "download_limited" not in stats
    expected = {
        "download_speed": 1,
        "upload_speed": 2,
        "download_limit": 3,
        "upload_limit": 4,
    }
    assert dict(stats.items()) == expected
    assert stats == expected
