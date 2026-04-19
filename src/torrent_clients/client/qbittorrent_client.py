"""qBittorrent client wrapper."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

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
from torrent_clients.torrent.torrent_info import TorrentInfo, TorrentList
from torrent_clients.torrent.torrent_peer import TorrentPeer, TorrentPeerList
from torrent_clients.torrent.torrent_status import (
    DownloaderKind,
    TorrentStatus,
    convert_status,
    is_completed,
    is_stopped,
)
from torrent_clients.torrent.torrent_tracker import TorrentTracker, TorrentTrackerList
from torrent_clients.transport.qbittorrent import QbittorrentTransport

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
_SUMMARY_FIELDS = [
    *_SNAPSHOT_FIELDS,
    "dlspeed",
    "upspeed",
    "uploaded",
    "num_leechs",
    "num_seeds",
    "category",
]


def _mapping_field_value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return default


def _normalize_snapshot_labels(labels: list[str]) -> list[str]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    return sorted(set(cleaned))


def _normalize_target_labels(labels: list[str]) -> list[str]:
    cleaned = [str(label).strip() for label in labels if str(label).strip()]
    return sorted(set(cleaned))


def _coerce_domain_status(status: str | None) -> TorrentStatus | None:
    if status is None:
        return None
    try:
        return TorrentStatus(status)
    except ValueError:
        return None


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

    def transform(self, torrent_data: Any) -> TorrentInfo:
        torrent_hash = require_adapter_field(torrent_data, "hash", context="qBittorrent torrent")
        torrent_name = require_adapter_field(torrent_data, "name", context="qBittorrent torrent")
        tags = optional_adapter_field(torrent_data, "tags", "")
        labels = _normalize_snapshot_labels(tags.split(",")) if tags else []
        status = convert_status(
            optional_adapter_field(torrent_data, "state", "unknown"),
            DownloaderKind.QBITTORRENT,
        )

        files_payload = _mapping_field_value(torrent_data, "files", None)
        trackers_payload = _mapping_field_value(torrent_data, "trackers", None)
        comment = _mapping_field_value(torrent_data, "comment", None)

        files = None
        if files_payload is not None:
            files = QbTorrentFileList(torrent_hash, raw=[files_payload])

        trackers = None
        if trackers_payload is not None:
            trackers = QbTorrentTrackerList(raw=trackers_payload)

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
            comment=comment,
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
        self.client = QbittorrentTransport(self.url, self.username, self.password)

    def _ensure_client(self) -> QbittorrentTransport | Any:
        if self.client is None:
            self.client = QbittorrentTransport(self.url, self.username, self.password)
        return self.client

    def _ensure_logged_in(self) -> Any:
        client = self._ensure_client()
        if hasattr(client, "auth_log_in"):
            client.auth_log_in()
        return client

    @staticmethod
    def _build_query_kwargs(
        status: str | None = None,
        query: TorrentQuery | None = None,
        *,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        query_kwargs: dict[str, Any] = {"status_filter": status}
        if fields is not None:
            query_kwargs["fields"] = list(fields)
        if query is not None:
            if query.fields is not None:
                warnings.warn(
                    "TorrentQuery.fields is deprecated; use get_torrents() for summaries, "
                    "get_torrent_info() for detail, and hydrate_*() for heavy fields.",
                    DeprecationWarning,
                    stacklevel=2,
                )
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
        return query_kwargs

    @staticmethod
    def _filter_by_domain_status(
        raw_torrents: Sequence[Any],
        status: TorrentStatus | None,
    ) -> list[Any]:
        if status is None:
            return list(raw_torrents)
        return [
            torrent_data
            for torrent_data in raw_torrents
            if convert_status(
                optional_adapter_field(torrent_data, "state", "unknown"),
                DownloaderKind.QBITTORRENT,
            )
            == status
        ]

    def _augment_torrents(
        self,
        torrent_ids: Sequence[str],
        *,
        include_files: bool = False,
        include_trackers: bool = False,
        include_comment: bool = False,
    ) -> list[dict[str, Any]]:
        client = self._ensure_logged_in()
        raw_torrents = list(
            client.torrents_info(
                **self._build_query_kwargs(
                    query=TorrentQuery(torrent_ids=list(torrent_ids)),
                    fields=_SUMMARY_FIELDS,
                )
            )
        )
        by_hash = {
            str(require_adapter_field(torrent_data, "hash", context="qBittorrent torrent")): {
                **dict(torrent_data),
                "hash": require_adapter_field(torrent_data, "hash", context="qBittorrent torrent"),
                "name": require_adapter_field(torrent_data, "name", context="qBittorrent torrent"),
            }
            for torrent_data in raw_torrents
        }
        for torrent_hash, torrent_data in by_hash.items():
            if include_files and hasattr(client, "torrents_files"):
                torrent_data["files"] = client.torrents_files(torrent_hash=torrent_hash)
            if include_trackers and hasattr(client, "torrents_trackers"):
                torrent_data["trackers"] = client.torrents_trackers(torrent_hash=torrent_hash)
            if include_comment:
                if hasattr(client, "torrents_properties"):
                    properties = client.torrents_properties(torrent_hash=torrent_hash)
                    torrent_data["comment"] = optional_adapter_field(properties, "comment", "")
                else:
                    torrent_data["comment"] = optional_adapter_field(torrent_data, "comment", "")
        return list(by_hash.values())

    def login(self) -> None:
        self._ensure_logged_in()

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
        client = self._ensure_logged_in()
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
            torrent_name = Path(torrent_url).name or "upload.torrent"
            result = client.torrents_add(torrent_files={torrent_name: payload}, **add_params)
        else:
            result = client.torrents_add(urls=payload, **add_params)
        if optional_adapter_field(result, "ok", False):
            return True
        return result == "Ok."

    def remove_torrent(self, torrent_id: TorrentIdInput, delete_data: bool = False) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self._ensure_logged_in().torrents_delete(
            torrent_hashes=torrent_hashes,
            delete_files=delete_data,
        )

    def get_torrents(
        self, status: str | None = None, query: TorrentQuery | None = None
    ) -> TorrentList:
        client = self._ensure_logged_in()
        target_status = _coerce_domain_status(status)
        raw_torrents = list(
            client.torrents_info(
                **self._build_query_kwargs(
                    None if target_status is not None else status,
                    query,
                )
            )
        )
        return QbTorrentList(raw=self._filter_by_domain_status(raw_torrents, target_status))

    def get_torrents_snapshot(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> list[TorrentSnapshot]:
        client = self._ensure_logged_in()
        target_status = _coerce_domain_status(status)
        snapshots: list[TorrentSnapshot] = []
        raw_torrents = self._filter_by_domain_status(
            list(
                client.torrents_info(
                    **self._build_query_kwargs(
                        None if target_status is not None else status,
                        query,
                        fields=_SNAPSHOT_FIELDS,
                    )
                )
            ),
            target_status,
        )
        for torrent_data in raw_torrents:
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
            status_value = convert_status(
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
                    status=status_value.value,
                    added_on=optional_adapter_field(torrent_data, "added_on", None),
                )
            )
        return snapshots

    def get_torrents_original(
        self,
        status: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        warnings.warn(
            "get_torrents_original() is deprecated; use get_torrents_snapshot() or "
            "get_torrents() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        client = self._ensure_logged_in()
        return list(client.torrents_info(status_filter=status, category=category))

    def move_torrent(
        self,
        torrent: TorrentInfo,
        download_dir: str,
        move_files: bool = True,
    ) -> bool:
        client = self._ensure_logged_in()
        _ = move_files
        status = torrent.status or TorrentStatus.UNKNOWN
        is_full_progress_paused = is_stopped(status) and is_completed(torrent.progress)

        client.torrents_set_location(torrent_hashes=torrent.id, location=download_dir)
        torrent_data = client.torrents_info(torrent_hashes=torrent.id)
        if not torrent_data:
            return False

        current_save_path = optional_adapter_field(torrent_data[0], "save_path", "")
        moved_successfully = current_save_path == download_dir.rstrip("/").rstrip("\\")

        if is_full_progress_paused:
            current_status = convert_status(
                optional_adapter_field(torrent_data[0], "state", "unknown"),
                DownloaderKind.QBITTORRENT,
            )
            current_progress = optional_adapter_field(torrent_data[0], "progress", 0)
            if is_stopped(current_status) and not is_completed(current_progress):
                client.torrents_stop(torrent_hashes=torrent.id)

        return moved_successfully

    def recheck_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self._ensure_logged_in().torrents_recheck(torrent_hashes=torrent_hashes)

    def get_torrent_info(self, torrent_id: int | str) -> Optional[TorrentInfo]:
        torrent_hash = str(torrent_id)
        client = self._ensure_logged_in()
        torrent_data = client.torrents_info(
            **self._build_query_kwargs(
                query=TorrentQuery(torrent_ids=[torrent_hash]),
                fields=_SUMMARY_FIELDS,
            )
        )
        if not torrent_data:
            return None

        payload = {
            **dict(torrent_data[0]),
            "hash": require_adapter_field(torrent_data[0], "hash", context="qBittorrent torrent"),
            "name": require_adapter_field(torrent_data[0], "name", context="qBittorrent torrent"),
        }
        if hasattr(client, "torrents_files"):
            payload["files"] = client.torrents_files(torrent_hash=torrent_hash)
        if hasattr(client, "torrents_trackers"):
            payload["trackers"] = client.torrents_trackers(torrent_hash=torrent_hash)
        if hasattr(client, "torrents_properties"):
            properties = client.torrents_properties(torrent_hash=torrent_hash)
            payload["comment"] = optional_adapter_field(properties, "comment", "")

        return QbTorrentList(raw=[payload]).details[0]

    def set_labels(self, torrent: TorrentInfo, labels: list[str]) -> None:
        client = self._ensure_logged_in()
        old_labels = torrent.labels or []
        deleted_labels = [label for label in old_labels if label not in labels]
        if deleted_labels:
            client.torrents_remove_tags(torrent_hashes=torrent.id, tags=deleted_labels)

        new_labels = [label for label in labels if label not in old_labels]
        if new_labels:
            client.torrents_add_tags(torrent_hashes=torrent.id, tags=new_labels)

    def set_labels_many(self, updates: Sequence[tuple[TorrentInfo, list[str]]]) -> None:
        client = self._ensure_logged_in()
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
            client.torrents_remove_tags(
                torrent_hashes=torrent_hashes,
                tags=list(labels_to_remove),
            )
        for labels_to_add, torrent_hashes in add_buckets.items():
            client.torrents_add_tags(
                torrent_hashes=torrent_hashes,
                tags=list(labels_to_add),
            )

    def hydrate_files(self, torrent_ids: TorrentIdInput) -> TorrentList:
        normalized_ids = [
            str(torrent_id) for torrent_id in self._normalize_torrent_ids(torrent_ids)
        ]
        client = self._ensure_logged_in()
        if not hasattr(client, "torrents_files"):
            return self.get_torrents(query=TorrentQuery(torrent_ids=normalized_ids))
        return QbTorrentList(raw=self._augment_torrents(normalized_ids, include_files=True))

    def hydrate_trackers(self, torrent_ids: TorrentIdInput) -> TorrentList:
        normalized_ids = [
            str(torrent_id) for torrent_id in self._normalize_torrent_ids(torrent_ids)
        ]
        client = self._ensure_logged_in()
        if not hasattr(client, "torrents_trackers"):
            return self.get_torrents(query=TorrentQuery(torrent_ids=normalized_ids))
        return QbTorrentList(raw=self._augment_torrents(normalized_ids, include_trackers=True))

    def set_category(self, torrent_id: int | str, category: str) -> None:
        torrent_hash = str(torrent_id)
        client = self._ensure_logged_in()
        categories = client.torrents_categories()
        if category not in list(categories):
            client.torrents_create_category(category)
        client.torrents_set_category(torrent_hashes=torrent_hash, category=category)

    def resume_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self._ensure_logged_in().torrents_start(torrent_hashes=torrent_hashes)

    def pause_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self._ensure_logged_in().torrents_stop(torrent_hashes=torrent_hashes)

    def reannounce_torrent(self, torrent_id: TorrentIdInput) -> None:
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        self._ensure_logged_in().torrents_reannounce(torrent_hashes=torrent_hashes)

    def set_torrent_limits(
        self,
        torrent_id: TorrentIdInput,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        client = self._ensure_logged_in()
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        if download_limit is not None:
            client.torrents_set_download_limit(
                limit=download_limit,
                torrent_hashes=torrent_hashes,
            )
        if upload_limit is not None:
            client.torrents_set_upload_limit(
                limit=upload_limit,
                torrent_hashes=torrent_hashes,
            )

    def move_queue(self, torrent_id: TorrentIdInput, direction: QueueDirection) -> None:
        client = self._ensure_logged_in()
        torrent_hashes = [
            str(torrent_hash) for torrent_hash in self._normalize_torrent_ids(torrent_id)
        ]
        queue_actions = {
            QueueDirection.TOP: client.torrents_top_priority,
            QueueDirection.BOTTOM: client.torrents_bottom_priority,
            QueueDirection.UP: client.torrents_increase_priority,
            QueueDirection.DOWN: client.torrents_decrease_priority,
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

        self._ensure_logged_in().torrents_file_priority(
            torrent_hash=str(torrent_id),
            file_ids=list(file_ids),
            priority=final_priority,
        )

    def list_trackers(self, torrent_id: TorrentId) -> TorrentTrackerList:
        tracker_data = self._ensure_logged_in().torrents_trackers(torrent_hash=str(torrent_id))
        return QbTorrentTrackerList(raw=tracker_data)

    def add_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self._ensure_logged_in().torrents_add_trackers(
            torrent_hash=str(torrent_id),
            urls=list(tracker_urls),
        )

    def remove_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self._ensure_logged_in().torrents_remove_trackers(
            torrent_hash=str(torrent_id),
            urls=list(tracker_urls),
        )

    def replace_tracker(self, torrent_id: TorrentId, old_url: str, new_url: str) -> None:
        self._ensure_logged_in().torrents_edit_tracker(
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
        client = self._ensure_logged_in()
        if new_name is not None:
            client.torrents_rename(
                torrent_hash=str(torrent_id),
                new_torrent_name=new_name,
            )
            return

        if old_path is not None and new_path is not None:
            client.torrents_rename_file(
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
        client = self._ensure_logged_in()
        if download_limit is not None:
            client.transfer_set_download_limit(limit=download_limit)
        if upload_limit is not None:
            client.transfer_set_upload_limit(limit=upload_limit)

    def get_client_stats(self) -> ClientStats:
        transfer_info = self._ensure_logged_in().transfer_info()
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
        peer_data = self._ensure_logged_in().sync_torrent_peers(torrent_hash=str(torrent_id))
        peers = optional_adapter_field(peer_data, "peers", [])
        return QbTorrentPeerList(raw=peers)

    def ban_ips(self, ips: Sequence[str]) -> None:
        client = self._ensure_logged_in()
        banned_ips_str = optional_adapter_field(client.app_preferences(), "banned_IPs", "")
        banned_ips = banned_ips_str.split("\n") if banned_ips_str else []
        for ip in ips:
            if ip not in banned_ips:
                banned_ips.append(ip)
        client.app_set_preferences(prefs={"banned_IPs": "\n".join(banned_ips)})
