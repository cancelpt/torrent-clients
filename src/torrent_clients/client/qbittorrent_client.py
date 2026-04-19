"""qBittorrent client wrapper."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Optional

from qbittorrentapi import Client, TorrentCategoriesDictionary, TorrentDictionary

from torrent_clients.client.base_client import (
    BaseClient,
    ClientStats,
    QueueDirection,
    TorrentId,
    TorrentIdInput,
    TorrentQuery,
    TorrentSnapshot,
    best_effort_adapter_field,
    optional_adapter_field,
    require_adapter_field,
)
from torrent_clients.torrent.torrent_file import TorrentFile, TorrentFileList
from torrent_clients.torrent.torrent_info import LazyProxy, TorrentInfo, TorrentList
from torrent_clients.torrent.torrent_peer import TorrentPeer, TorrentPeerList
from torrent_clients.torrent.torrent_status import (
    DownloaderKind,
    TorrentStatus,
    convert_status,
    is_completed,
    is_stopped,
)
from torrent_clients.torrent.torrent_tracker import TorrentTracker, TorrentTrackerList

logger = logging.getLogger(__name__)

_SNAPSHOT_FIELDS = [
    "hash",
    "name",
    "save_path",
    "progress",
    "total_size",
    "size",
    "completed",
    "tags",
    "state",
    "added_on",
]


def _normalize_snapshot_labels(labels: list[str]) -> list[str]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    return sorted(set(cleaned))


def _normalize_target_labels(labels: list[str]) -> list[str]:
    cleaned = [str(label).strip() for label in labels if str(label).strip()]
    return sorted(set(cleaned))


class QbTorrentPeerList(TorrentPeerList):
    """qBittorrent peer list adapter."""

    def transform(self, peer_data: dict[str, Any]) -> Optional[TorrentPeer]:
        return TorrentPeer(
            client=peer_data.get("client", ""),
            dl_speed=peer_data.get("dl_speed", 0),
            downloaded=peer_data.get("downloaded", 0),
            up_speed=peer_data.get("up_speed", 0),
            uploaded=peer_data.get("uploaded", 0),
            ip=peer_data.get("ip", ""),
            port=peer_data.get("port", 0),
            progress=peer_data.get("progress", 0.0),
            flags=peer_data.get("flags", ""),
        )


class QbTorrentFileList(TorrentFileList):
    """qBittorrent file list adapter."""

    def transform(self, file_data: list[Any]) -> TorrentFile:
        file_item = file_data[0]
        raw_priority = file_item.get("priority")
        priority = int(raw_priority) if raw_priority is not None else 0
        wanted = True if raw_priority is None else priority != 0
        return TorrentFile(
            name=file_item.get("name", ""),
            size=file_item.get("size"),
            priority=priority,
            wanted=wanted,
            progress=file_item.get("progress"),
        )

    def iter_file_entries(self):  # type: ignore[no-untyped-def]
        for file_item in self.raw[0] if self.raw else []:
            raw_name = str(file_item.get("name", "") or "")
            if not raw_name:
                continue
            yield {
                "path": raw_name.replace("\\", "/"),
                "origin": raw_name,
                "size": int(file_item.get("size", 0) or 0),
            }


class QbTorrentTrackerList(TorrentTrackerList):
    """qBittorrent tracker list adapter."""

    def transform(self, tracker_data: dict[str, Any]) -> Optional[TorrentTracker]:
        tracker_url = optional_adapter_field(tracker_data, "url", "")
        if re.match(r"^(udp|http|https)://", tracker_url):
            return TorrentTracker(
                url=tracker_url,
                downloaded=optional_adapter_field(tracker_data, "num_downloaded", -1),
                seeder=optional_adapter_field(tracker_data, "num_seeds", -1),
                leecher=optional_adapter_field(tracker_data, "num_leeches", -1),
                peers=optional_adapter_field(tracker_data, "num_peers", -1),
                info=optional_adapter_field(tracker_data, "msg", ""),
            )
        return None


class QbTorrentList(TorrentList):
    """qBittorrent torrent list adapter."""

    def transform(self, torrent_data: TorrentDictionary) -> TorrentInfo:
        # Required identifiers must be present; other fields keep stable defaults/sentinels.
        torrent_hash = require_adapter_field(torrent_data, "hash", context="qBittorrent torrent")
        torrent_name = require_adapter_field(torrent_data, "name", context="qBittorrent torrent")
        tags = optional_adapter_field(torrent_data, "tags", "")
        labels = [label.strip() for label in tags.split(",")] if tags else []
        status = convert_status(
            optional_adapter_field(torrent_data, "state", "unknown"),
            DownloaderKind.QBITTORRENT,
        )

        files = LazyProxy(lambda: QbTorrentFileList(torrent_hash, raw=[torrent_data.files]))
        trackers = LazyProxy(lambda: QbTorrentTrackerList(raw=torrent_data.trackers))

        return TorrentInfo(
            id=torrent_hash,
            name=torrent_name,
            hash_string=torrent_hash,
            download_dir=optional_adapter_field(torrent_data, "save_path", None),
            size=optional_adapter_field(torrent_data, "total_size", 0),
            progress=optional_adapter_field(torrent_data, "progress", 0),
            status=status,
            download_speed=optional_adapter_field(torrent_data, "dlspeed", 0),
            upload_speed=optional_adapter_field(torrent_data, "upspeed", 0),
            labels=labels,
            files=files,
            trackers=trackers,
            completed_size=optional_adapter_field(torrent_data, "completed", 0),
            selected_size=optional_adapter_field(torrent_data, "size", 0),
            category=optional_adapter_field(torrent_data, "category", ""),
            uploaded_size=optional_adapter_field(torrent_data, "uploaded", 0),
            num_leechs=optional_adapter_field(torrent_data, "num_leechs", -1),
            num_seeds=optional_adapter_field(torrent_data, "num_seeds", -1),
            added_on=optional_adapter_field(torrent_data, "added_on", -1),
            comment=LazyProxy(
                lambda: optional_adapter_field(torrent_data.properties, "comment", "")
            ),
        )


class QbittorrentClient(BaseClient):
    """qBittorrent downloader client."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        dl_type: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(url, username, password, dl_type, name)
        self.dl_type = "qb"
        self.client = Client(host=self.url, username=self.username, password=self.password)

    def login(self) -> None:
        self.client.auth_log_in()

    def add_torrent(
        self,
        torrent_url: str,
        upload_limit: int | None = None,
        download_limit: int | None = None,
        download_dir: str | None = None,
        is_paused: bool = False,
        skip_checking: bool = False,
        label: str | None = None,
        forced: bool = False,
    ) -> bool:
        add_params = {
            "save_path": download_dir,
            "is_paused": is_paused,
            "download_limited": download_limit is not None,
            "upload_limited": upload_limit is not None,
            "upload_limit": upload_limit,
            "download_limit": download_limit,
            "skip_checking": skip_checking,
            "category": label if label else "",
            "forced": forced,
        }

        try:
            payload_type, payload = self._prepare_torrent_input(torrent_url)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("%s", exc)
            return False

        if payload_type == "file":
            result = self.client.torrents_add(torrent_files=payload, **add_params)
        else:
            result = self.client.torrents_add(urls=payload, **add_params)
        return result == "Ok."

    def remove_torrent(self, torrent_id: TorrentIdInput, delete_data: bool = False) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self.client.torrents_delete(torrent_hashes=torrent_hashes, delete_files=delete_data)

    def get_torrents(
        self, status: str | None = None, query: TorrentQuery | None = None
    ) -> TorrentList:
        if self.client is None or not self.client.auth_log_in():
            self.login()
            logger.info("Successfully connected to qbittorrent: %s", self.url)

        query_kwargs: dict[str, Any] = {"status_filter": status}
        if query is not None:
            if query.category is not None:
                query_kwargs["category"] = query.category
            if query.tag is not None:
                query_kwargs["tag"] = query.tag
            if query.sort is not None:
                query_kwargs["sort"] = query.sort
            if query.reverse is not None:
                query_kwargs["reverse"] = query.reverse
            if query.limit is not None:
                query_kwargs["limit"] = query.limit
            if query.offset is not None:
                query_kwargs["offset"] = query.offset
            if query.torrent_ids is not None:
                query_kwargs["torrent_hashes"] = [str(item) for item in query.torrent_ids]

        return QbTorrentList(raw=self.client.torrents_info(**query_kwargs))

    def get_torrents_snapshot(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> list[TorrentSnapshot]:
        if self.client is None or not self.client.auth_log_in():
            self.login()
            logger.info("Successfully connected to qbittorrent: %s", self.url)

        query_kwargs: dict[str, Any] = {"status_filter": status, "fields": _SNAPSHOT_FIELDS}
        if query is not None:
            if query.category is not None:
                query_kwargs["category"] = query.category
            if query.tag is not None:
                query_kwargs["tag"] = query.tag
            if query.sort is not None:
                query_kwargs["sort"] = query.sort
            if query.reverse is not None:
                query_kwargs["reverse"] = query.reverse
            if query.limit is not None:
                query_kwargs["limit"] = query.limit
            if query.offset is not None:
                query_kwargs["offset"] = query.offset
            if query.torrent_ids is not None:
                query_kwargs["torrent_hashes"] = [str(item) for item in query.torrent_ids]

        snapshots: list[TorrentSnapshot] = []
        for torrent_data in self.client.torrents_info(**query_kwargs):
            torrent_hash = require_adapter_field(
                torrent_data,
                "hash",
                context="qBittorrent snapshot",
            )
            torrent_name = require_adapter_field(
                torrent_data,
                "name",
                context="qBittorrent snapshot",
            )
            tags = optional_adapter_field(torrent_data, "tags", "")
            labels = _normalize_snapshot_labels(tags.split(",")) if tags else []
            status = convert_status(
                optional_adapter_field(torrent_data, "state", "unknown"),
                DownloaderKind.QBITTORRENT,
            )
            snapshots.append(
                TorrentSnapshot(
                    id=torrent_hash,
                    hash_string=torrent_hash,
                    name=torrent_name,
                    download_dir=optional_adapter_field(torrent_data, "save_path", ""),
                    progress=optional_adapter_field(torrent_data, "progress", 0),
                    size=optional_adapter_field(torrent_data, "total_size", 0),
                    selected_size=optional_adapter_field(torrent_data, "size", 0),
                    completed_size=optional_adapter_field(torrent_data, "completed", 0),
                    labels=labels,
                    status=status.value,
                    added_on=optional_adapter_field(torrent_data, "added_on", None),
                )
            )

        return snapshots

    def get_torrents_original(
        self,
        status: str | None = None,
        category: str | None = None,
    ) -> list[TorrentDictionary]:
        if self.client is None or not self.client.auth_log_in():
            self.login()
            logger.debug("Successfully connected to qbittorrent: %s", self.url)
        return list(self.client.torrents_info(status_filter=status, category=category))

    def move_torrent(
        self,
        torrent: TorrentInfo,
        download_dir: str,
        move_files: bool = True,
    ) -> bool:
        _ = move_files  # qB API does not expose move/copy toggle.
        status = torrent.status or TorrentStatus.UNKNOWN
        is_full_progress_paused = is_stopped(status) and is_completed(torrent.progress)

        self.client.torrents_set_location(torrent_hashes=torrent.id, location=download_dir)
        torrent_data = self.client.torrents_info(torrent_hashes=torrent.id)
        if not torrent_data:
            return False

        current_save_path = torrent_data[0].get("save_path", "")
        moved_successfully = current_save_path == download_dir.rstrip("/").rstrip("\\")

        if is_full_progress_paused:
            current_status = convert_status(torrent_data[0].state, DownloaderKind.QBITTORRENT)
            current_progress = torrent_data[0].get("progress", 0)
            if is_stopped(current_status) and not is_completed(current_progress):
                self.client.torrents_stop(torrent_hashes=torrent.id)

        return moved_successfully

    def recheck_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self.client.torrents_recheck(torrent_hashes=torrent_hashes)

    def get_torrent_info(self, torrent_id: int | str) -> Optional[TorrentInfo]:
        torrent_hash = str(torrent_id)
        torrent_data = self.client.torrents_info(torrent_hashes=torrent_hash)
        qb_torrents = QbTorrentList(raw=torrent_data).details
        return qb_torrents[0] if qb_torrents else None

    def set_labels(self, torrent: TorrentInfo, labels: list[str]) -> None:
        old_labels = torrent.labels or []
        deleted_labels = [label for label in old_labels if label not in labels]
        if deleted_labels:
            self.client.torrents_remove_tags(torrent_hashes=torrent.id, tags=deleted_labels)

        new_labels = [label for label in labels if label not in old_labels]
        if new_labels:
            self.client.torrents_add_tags(torrent_hashes=torrent.id, tags=new_labels)

    def set_labels_many(self, updates: Sequence[tuple[TorrentInfo, list[str]]]) -> None:
        add_buckets: dict[tuple[str, ...], list[str]] = {}
        remove_buckets: dict[tuple[str, ...], list[str]] = {}

        for torrent, labels in updates:
            current_labels = _normalize_target_labels(list(torrent.labels or []))
            target_labels = _normalize_target_labels(labels)
            labels_to_remove = tuple(
                label for label in current_labels if label not in target_labels
            )
            labels_to_add = tuple(label for label in target_labels if label not in current_labels)

            if labels_to_remove:
                remove_buckets.setdefault(labels_to_remove, []).append(str(torrent.id))
            if labels_to_add:
                add_buckets.setdefault(labels_to_add, []).append(str(torrent.id))

        for labels_to_remove, torrent_hashes in remove_buckets.items():
            self.client.torrents_remove_tags(
                torrent_hashes=torrent_hashes,
                tags=list(labels_to_remove),
            )
        for labels_to_add, torrent_hashes in add_buckets.items():
            self.client.torrents_add_tags(
                torrent_hashes=torrent_hashes,
                tags=list(labels_to_add),
            )

    def hydrate_files(self, torrent_ids: TorrentIdInput) -> TorrentList:
        return self.get_torrents(
            query=TorrentQuery(
                torrent_ids=[
                    str(torrent_id) for torrent_id in self._normalize_torrent_ids(torrent_ids)
                ]
            )
        )

    def hydrate_trackers(self, torrent_ids: TorrentIdInput) -> TorrentList:
        return self.get_torrents(
            query=TorrentQuery(
                torrent_ids=[
                    str(torrent_id) for torrent_id in self._normalize_torrent_ids(torrent_ids)
                ]
            )
        )

    def set_category(self, torrent_id: int | str, category: str) -> None:
        torrent_hash = str(torrent_id)
        categories: TorrentCategoriesDictionary = self.client.torrents_categories()
        if category not in list(categories):
            self.client.torrents_create_category(category)
        self.client.torrents_set_category(torrent_hashes=torrent_hash, category=category)

    def resume_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self.client.torrents_start(torrent_hashes=torrent_hashes)

    def pause_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self.client.torrents_stop(torrent_hashes=torrent_hashes)

    def reannounce_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self.client.torrents_reannounce(torrent_hashes=torrent_hashes)

    def set_torrent_limits(
        self,
        torrent_id: TorrentIdInput,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        if download_limit is not None:
            self.client.torrents_set_download_limit(
                limit=download_limit,
                torrent_hashes=torrent_hashes,
            )
        if upload_limit is not None:
            self.client.torrents_set_upload_limit(
                limit=upload_limit,
                torrent_hashes=torrent_hashes,
            )

    def move_queue(self, torrent_id: TorrentIdInput, direction: QueueDirection) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        queue_actions = {
            QueueDirection.TOP: self.client.torrents_top_priority,
            QueueDirection.BOTTOM: self.client.torrents_bottom_priority,
            QueueDirection.UP: self.client.torrents_increase_priority,
            QueueDirection.DOWN: self.client.torrents_decrease_priority,
        }
        queue_actions[direction](torrent_hashes=torrent_hashes)

    def set_files(
        self,
        torrent_id: TorrentId,
        file_ids: Sequence[int],
        wanted: bool | None = None,
        priority: int | None = None,
    ) -> None:
        if not file_ids:
            return

        if wanted is False:
            final_priority = 0
        elif priority is not None:
            final_priority = priority
        elif wanted is True:
            final_priority = 1
        else:
            raise ValueError("set_files requires wanted or priority")

        self.client.torrents_file_priority(
            torrent_hash=str(torrent_id),
            file_ids=list(file_ids),
            priority=final_priority,
        )

    def list_trackers(self, torrent_id: TorrentId) -> TorrentTrackerList:
        tracker_data = self.client.torrents_trackers(torrent_hash=str(torrent_id))
        return QbTorrentTrackerList(raw=tracker_data)

    def add_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self.client.torrents_add_trackers(torrent_hash=str(torrent_id), urls=list(tracker_urls))

    def remove_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self.client.torrents_remove_trackers(torrent_hash=str(torrent_id), urls=list(tracker_urls))

    def replace_tracker(self, torrent_id: TorrentId, old_url: str, new_url: str) -> None:
        self.client.torrents_edit_tracker(
            torrent_hash=str(torrent_id),
            original_url=old_url,
            new_url=new_url,
        )

    def rename_torrent(
        self,
        torrent_id: TorrentId,
        new_name: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> None:
        if new_name is not None:
            self.client.torrents_rename(
                torrent_hash=str(torrent_id),
                new_torrent_name=new_name,
            )
            return

        if old_path is not None and new_path is not None:
            self.client.torrents_rename_file(
                torrent_hash=str(torrent_id),
                old_path=old_path,
                new_path=new_path,
            )
            return

        raise ValueError("rename_torrent requires new_name or old_path/new_path")

    def set_global_limits(
        self,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        if download_limit is not None:
            self.client.transfer_set_download_limit(limit=download_limit)
        if upload_limit is not None:
            self.client.transfer_set_upload_limit(limit=upload_limit)

    def get_client_stats(self) -> ClientStats:
        transfer_info = self.client.transfer_info()
        return ClientStats(
            download_speed=best_effort_adapter_field(
                transfer_info,
                "dl_info_speed",
                0,
                logger=logger,
                context="qBittorrent client stats",
            ),
            upload_speed=best_effort_adapter_field(
                transfer_info,
                "up_info_speed",
                0,
                logger=logger,
                context="qBittorrent client stats",
            ),
            download_limit=best_effort_adapter_field(
                transfer_info,
                "dl_rate_limit",
                0,
                logger=logger,
                context="qBittorrent client stats",
            ),
            upload_limit=best_effort_adapter_field(
                transfer_info,
                "up_rate_limit",
                0,
                logger=logger,
                context="qBittorrent client stats",
            ),
        )

    def get_peer_info(self, torrent_id: int | str) -> TorrentPeerList:
        peer_data = self.client.sync_torrent_peers(torrent_hash=str(torrent_id))
        return QbTorrentPeerList(raw=peer_data.peers)

    def ban_ips(self, ips: Sequence[str]) -> None:
        banned_ips_str = self.client.app_preferences()["banned_IPs"]
        banned_ips = banned_ips_str.split("\n")
        for ip in ips:
            if ip not in banned_ips:
                banned_ips.append(ip)
        self.client.app_set_preferences(prefs={"banned_IPs": "\n".join(banned_ips)})
