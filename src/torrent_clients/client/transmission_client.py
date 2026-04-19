from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable, Sequence
from typing import Any, Dict, List, Optional

from torrent_clients.client.base_client import (
    BaseClient,
    ClientStats,
    QueueDirection,
    SupportsCategoryManagement,
    TorrentId,
    TorrentIdInput,
    TorrentQuery,
    TorrentSnapshot,
    UnsupportedClientCapabilityError,
    best_effort_adapter_field,
    optional_adapter_field,
    require_adapter_field,
)
from torrent_clients.torrent.torrent_file import TorrentFile, TorrentFileList
from torrent_clients.torrent.torrent_info import TorrentInfo, TorrentList
from torrent_clients.torrent.torrent_peer import TorrentPeer, TorrentPeerList
from torrent_clients.torrent.torrent_status import DownloaderKind, TorrentStatus, convert_status
from torrent_clients.torrent.torrent_tracker import TorrentTracker, TorrentTrackerList
from torrent_clients.transport.errors import (
    TransportAuthenticationError,
    TransportConnectionError,
    TransportProtocolError,
)
from torrent_clients.transport.transmission import TransmissionTransport

logger = logging.getLogger(__name__)

invalid_file_name_pattern = re.compile(r"[\u1F00-\u1FFF]")
_DEFAULT_EAGER_SCALAR_ARGUMENTS = (
    "id",
    "name",
    "status",
    "percentDone",
    "rateDownload",
    "rateUpload",
    "downloadDir",
    "labels",
    "comment",
    "totalSize",
    "haveValid",
    "uploadedEver",
    "sizeWhenDone",
    "peersSendingToUs",
    "peersGettingFromUs",
    "addedDate",
    "hashString",
)
_SUMMARY_FIELDS = (
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
    "rateDownload",
    "rateUpload",
    "uploadedEver",
    "peersSendingToUs",
    "peersGettingFromUs",
)
_DETAIL_FIELDS = (
    *_SUMMARY_FIELDS,
    "comment",
    "files",
    "fileStats",
    "trackerStats",
)
_HYDRATE_FILE_FIELDS = (*_SUMMARY_FIELDS, "files", "fileStats")
_HYDRATE_TRACKER_FIELDS = (*_SUMMARY_FIELDS, "trackerStats")
_LAZY_GROUP_FIELDS = {
    "files": ("files", "fileStats"),
    "trackers": ("trackerStats",),
}
_DEFAULT_LAZY_PROMOTE_THRESHOLD = {
    "files": 24,
    "trackers": 24,
}
_SNAPSHOT_FIELDS = (
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
)


def _available_torrent_fields(torrent_data: Any) -> set[str]:
    declared_fields = optional_adapter_field(torrent_data, "__fields__", None)
    if declared_fields:
        return {str(field) for field in declared_fields}

    runtime_fields = getattr(torrent_data, "fields", None)
    if runtime_fields:
        return {str(field) for field in runtime_fields}

    if isinstance(torrent_data, dict):
        return {str(field) for field in torrent_data.keys() if field != "__fields__"}

    return set()


def _normalize_snapshot_labels(labels: list[str]) -> list[str]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    return sorted(set(cleaned))


def _normalize_target_labels(labels: list[str]) -> list[str]:
    cleaned = [str(label).strip() for label in labels if str(label).strip()]
    return sorted(set(cleaned))


class MissingTorrentFieldError(RuntimeError):
    """Raised when strict arguments mode accesses a field that was not requested."""


def find_invalid_characters(s: str) -> list[str]:
    return [char for char in s if invalid_file_name_pattern.match(char)]


class TrTorrentPeerList(TorrentPeerList):
    def transform(self, peer_data: Dict[str, Any]) -> Optional[TorrentPeer]:
        return TorrentPeer(
            client=peer_data.get("client") or peer_data.get("clientName", ""),
            dl_speed=peer_data.get("rateToClient"),
            downloaded=peer_data.get("downloaded", peer_data.get("downloadedEver", 0)),
            up_speed=peer_data.get("rateToPeer"),
            uploaded=peer_data.get("uploaded", peer_data.get("uploadedEver", 0)),
            ip=peer_data.get("address"),
            port=peer_data.get("port", 0),
            progress=peer_data.get("progress", 0.0),
            flags=peer_data.get("flagStr", ""),
        )


class TrTorrentFileList(TorrentFileList):
    def transform(self, file_data: List[Any]) -> TorrentFile:
        files = file_data[0]
        file_stats = file_data[1] if len(file_data) > 1 else {}
        file_name = files.get("name", "")
        if find_invalid_characters(file_name):
            logger.debug(
                "torrent %s has invalid filename characters: %s", self.torrent_id, file_name
            )

        return TorrentFile(
            name=files.get("name", ""),
            size=files.get("length"),
            priority=file_stats.get("priority", -1),
            wanted=file_stats.get("wanted", True),
            completed_size=files.get("bytesCompleted", 0),
        )

    def iter_file_entries(self):  # type: ignore[no-untyped-def]
        for files in self.raw[0] if self.raw else []:
            raw_name = str(files.get("name", "") or "")
            if not raw_name:
                continue
            yield {
                "path": raw_name.replace("\\", "/"),
                "origin": raw_name,
                "size": int(files.get("length", 0) or 0),
            }


class TrTorrentTrackerList(TorrentTrackerList):
    def transform(self, tracker_data: Dict[str, Any]) -> Optional[TorrentTracker]:
        announce_url = optional_adapter_field(tracker_data, "announce", "")
        if self.valid_url_pattern.match(announce_url):
            default_count = -1
            return TorrentTracker(
                url=announce_url,
                downloaded=optional_adapter_field(tracker_data, "downloaded", default_count),
                seeder=optional_adapter_field(tracker_data, "seeder_count", default_count),
                leecher=optional_adapter_field(tracker_data, "leecher_count", default_count),
                peers=default_count,
                info=optional_adapter_field(tracker_data, "last_announce_result", ""),
            )
        return None


class TrTorrentList(TorrentList):
    def transform(self, torrent_data: Any) -> TorrentInfo:
        torrent_id = require_adapter_field(torrent_data, "id", context="Transmission torrent")
        name = require_adapter_field(torrent_data, "name", context="Transmission torrent")

        if find_invalid_characters(name):
            logger.warning("torrent %s has invalid name characters: %s", torrent_id, name)

        available_fields = _available_torrent_fields(torrent_data)

        files = None
        if "files" in available_fields:
            files_payload = list(optional_adapter_field(torrent_data, "files", []) or [])
            file_stats_payload = list(optional_adapter_field(torrent_data, "fileStats", []) or [])
            if len(file_stats_payload) == len(files_payload):
                files = TrTorrentFileList(torrent_id, raw=[files_payload, file_stats_payload])
            else:
                files = TrTorrentFileList(torrent_id, raw=[files_payload])

        trackers = None
        if "trackerStats" in available_fields:
            trackers = TrTorrentTrackerList(
                raw=list(optional_adapter_field(torrent_data, "trackerStats", []) or [])
            )

        labels = list(optional_adapter_field(torrent_data, "labels", []) or [])
        category = labels[0] if labels else ""
        origin_status = optional_adapter_field(torrent_data, "status", "unknown")
        status = convert_status(origin_status, DownloaderKind.TRANSMISSION)
        comment = None
        if "comment" in available_fields:
            comment = optional_adapter_field(torrent_data, "comment", "")

        return TorrentInfo(
            id=torrent_id,
            name=name,
            hash_string=optional_adapter_field(torrent_data, "hashString", ""),
            download_dir=optional_adapter_field(torrent_data, "downloadDir", ""),
            size=optional_adapter_field(torrent_data, "totalSize", 0),
            progress=optional_adapter_field(torrent_data, "percentDone", 0),
            status=status,
            download_speed=optional_adapter_field(torrent_data, "rateDownload", 0),
            upload_speed=optional_adapter_field(torrent_data, "rateUpload", 0),
            files=files,
            trackers=trackers,
            labels=labels,
            category=category,
            completed_size=optional_adapter_field(torrent_data, "haveValid", 0),
            uploaded_size=optional_adapter_field(torrent_data, "uploadedEver", 0),
            selected_size=optional_adapter_field(torrent_data, "sizeWhenDone", 0),
            num_seeds=optional_adapter_field(torrent_data, "peersSendingToUs", -1),
            num_leechs=optional_adapter_field(torrent_data, "peersGettingFromUs", -1),
            added_on=optional_adapter_field(torrent_data, "addedDate", -1),
            comment=comment,
        )


class TrLazyTorrentInfo:
    def __init__(self, resolver: "TrLazyFieldResolver", torrent_id: int):
        self._resolver = resolver
        self._torrent_id = int(torrent_id)

    @property
    def id(self) -> int:
        return self._torrent_id

    @property
    def size(self) -> int:
        self._resolver.ensure_fields(self.id, ["totalSize"])
        return int(self._resolver.get_value(self.id, "totalSize", 0) or 0)

    @property
    def name(self) -> str:
        self._resolver.ensure_fields(self.id, ["name"])
        return self._resolver.get_value(self.id, "name", "") or ""

    @property
    def status(self) -> TorrentStatus:
        self._resolver.ensure_fields(self.id, ["status"])
        return convert_status(
            self._resolver.get_value(self.id, "status", "unknown"),
            DownloaderKind.TRANSMISSION,
        )

    @property
    def files(self) -> TrTorrentFileList:
        self._resolver.ensure_group(self.id, "files")
        files = self._resolver.get_value(self.id, "files", []) or []
        file_stats = self._resolver.get_value(self.id, "fileStats", []) or []
        if len(file_stats) == len(files):
            return TrTorrentFileList(self.id, raw=[files, file_stats])
        return TrTorrentFileList(self.id, raw=[files])

    @property
    def trackers(self) -> TrTorrentTrackerList:
        self._resolver.ensure_group(self.id, "trackers")
        tracker_stats = self._resolver.get_value(self.id, "trackerStats", []) or []
        return TrTorrentTrackerList(raw=tracker_stats)


class TrLazyFieldResolver:
    def __init__(
        self,
        client: Any,
        torrent_ids: list[int],
        strict_mode: bool = False,
        batch_size: int = 200,
        promote_thresholds: Optional[dict[str, int]] = None,
    ):
        self._client = client
        self._torrent_ids = torrent_ids
        self._strict_mode = strict_mode
        self._batch_size = max(1, int(batch_size))
        self._values: dict[int, dict[str, Any]] = {torrent_id: {} for torrent_id in torrent_ids}
        self._loaded_fields: dict[int, set[str]] = {torrent_id: set() for torrent_id in torrent_ids}

        merged_thresholds = dict(_DEFAULT_LAZY_PROMOTE_THRESHOLD)
        if promote_thresholds:
            merged_thresholds.update(
                {key: int(value) for key, value in promote_thresholds.items() if value > 0}
            )
        self._promote_thresholds = merged_thresholds

        self._group_access_count = {group: 0 for group in _LAZY_GROUP_FIELDS}
        self._group_promoted = {group: False for group in _LAZY_GROUP_FIELDS}
        self._group_pending_ids = {group: set(torrent_ids) for group in _LAZY_GROUP_FIELDS}

    def seed_from_torrent(self, torrent_data: Any) -> None:
        torrent_id = int(
            require_adapter_field(torrent_data, "id", context="Transmission lazy torrent")
        )

        loaded = self._loaded_fields.setdefault(torrent_id, set())
        values = self._values.setdefault(torrent_id, {})
        fields = _available_torrent_fields(torrent_data)

        for field in fields:
            values[field] = optional_adapter_field(torrent_data, field, None)
            loaded.add(field)

        values["id"] = torrent_id
        loaded.add("id")

        for group, group_fields in _LAZY_GROUP_FIELDS.items():
            if all(field in loaded for field in group_fields):
                self._group_pending_ids[group].discard(torrent_id)

    def ensure_group(self, torrent_id: int, group: str) -> None:
        group_fields = list(_LAZY_GROUP_FIELDS[group])
        loaded = self._loaded_fields[torrent_id]
        missing = [field for field in group_fields if field not in loaded]
        if not missing:
            return

        if self._strict_mode:
            self._raise_missing_fields(missing)

        self._group_access_count[group] += 1
        threshold = self._promote_thresholds.get(group, 1)
        should_promote = self._group_access_count[group] >= threshold

        if should_promote and not self._group_promoted[group]:
            self._group_promoted[group] = True
            self._fetch_pending_group(group)
            return

        self._fetch_ids_for_fields([torrent_id], group_fields)

    def ensure_fields(self, torrent_id: int, fields: Iterable[str]) -> None:
        missing = [field for field in fields if field not in self._loaded_fields[torrent_id]]
        if not missing:
            return

        if self._strict_mode:
            self._raise_missing_fields(missing)

        self._fetch_ids_for_fields([torrent_id], missing)

    def get_value(self, torrent_id: int, field: str, default: Any = None) -> Any:
        return self._values[torrent_id].get(field, default)

    @staticmethod
    def _raise_missing_fields(fields: Iterable[str]) -> None:
        missing = ", ".join(sorted(set(fields)))
        raise MissingTorrentFieldError(
            f"Missing field(s): {missing}. "
            "Custom arguments mode is strict and disables lazy/hybrid auto fetch."
        )

    def _fetch_pending_group(self, group: str) -> None:
        pending = self._group_pending_ids[group]
        if not pending:
            return
        pending_ids = [torrent_id for torrent_id in self._torrent_ids if torrent_id in pending]
        self._fetch_ids_for_fields(pending_ids, _LAZY_GROUP_FIELDS[group])

    def _fetch_ids_for_fields(self, ids: list[int], fields: Iterable[str]) -> None:
        if not ids:
            return

        requested_fields = ["id", *list(fields)]
        for idx in range(0, len(ids), self._batch_size):
            batch_ids = ids[idx : idx + self._batch_size]
            torrents = self._client.get_torrents(ids=batch_ids, arguments=requested_fields)
            for torrent_data in torrents:
                self.seed_from_torrent(torrent_data)


class TrLazyTorrentList(Sequence):
    def __init__(self, raw: list[TrLazyTorrentInfo] | None = None):
        self.raw = raw or []

    def __iter__(self):
        return iter(self.raw)

    def __len__(self) -> int:
        return len(self.raw)

    def __getitem__(self, index: int | slice):
        return self.raw[index]

    @property
    def details(self) -> list[TrLazyTorrentInfo]:
        return list(self.raw)


class TransmissionClient(BaseClient):
    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        dl_type: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(url, username, password, dl_type, name)
        self.client = TransmissionTransport(self.url, self.username, self.password)
        self.dl_type = "transmission"

    def _ensure_client(self) -> TransmissionTransport | Any:
        if self.client is None:
            self.client = TransmissionTransport(self.url, self.username, self.password)
        return self.client

    def login(self) -> bool:
        client = self._ensure_client()
        if not hasattr(client, "get_session"):
            return True
        try:
            client.get_session()
        except (TransportAuthenticationError, TransportConnectionError, TransportProtocolError):
            return False
        return True

    def supports_capability(self, capability: type[Any]) -> bool:
        if capability is SupportsCategoryManagement:
            return False
        return super().supports_capability(capability)

    def require_capability(self, capability: type[Any]):
        if capability is SupportsCategoryManagement:
            raise UnsupportedClientCapabilityError(
                f"{self.__class__.__name__} does not support capability "
                f"{getattr(capability, '__name__', capability)}"
            )
        return super().require_capability(capability)

    def add_torrent(
        self,
        torrent_url: str,
        upload_limit: int | None = None,
        download_limit: int | None = None,
        download_dir: str | None = None,
        is_paused: bool = True,
        skip_checking: bool = False,
    ) -> bool:
        client = self._ensure_client()

        torrent_input = torrent_url
        try:
            _, torrent_input = self._prepare_torrent_input(torrent_input)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("%s", exc)
            return False

        try:
            result = client.add_torrent(torrent_input, download_dir=download_dir, paused=True)
        except (
            TransportAuthenticationError,
            TransportConnectionError,
            TransportProtocolError,
        ) as exc:
            logger.error("add torrent failed: %s", exc)
            return False

        if not result:
            logger.error("add torrent failed: %s", torrent_input)
            return False

        torrent_id = optional_adapter_field(result, "id", None)
        if torrent_id is None:
            return False

        if upload_limit is None:
            upload_limit = -1
        if download_limit is None:
            download_limit = -1

        retry = 3
        while retry > 0:
            client.change_torrent(
                torrent_id,
                upload_limit=upload_limit,
                download_limit=download_limit,
                upload_limited=(upload_limit != -1),
                download_limited=(download_limit != -1),
            )
            torrent = client.get_torrent(
                torrent_id, arguments=["id", "upload_limit", "download_limit"]
            )
            current_upload_limit = optional_adapter_field(torrent, "upload_limit", None)
            current_download_limit = optional_adapter_field(torrent, "download_limit", None)
            if (
                (upload_limit != -1 and current_upload_limit == upload_limit) or upload_limit == -1
            ) and (
                (download_limit != -1 and current_download_limit == download_limit)
                or download_limit == -1
            ):
                break
            retry -= 1
        if retry == 0:
            logger.error("cannot set upload/download limit for torrent: %s", torrent_id)
            return False

        if skip_checking:
            client.reannounce_torrent(torrent_id)
        if not is_paused:
            client.start_torrent(torrent_id)
        return True

    def remove_torrent(self, torrent_id: TorrentIdInput, delete_data: bool = False) -> None:
        self._ensure_client().remove_torrent(
            self._normalize_torrent_ids(torrent_id),
            delete_data=delete_data,
        )

    def get_torrents(
        self, status: str | None = None, query: TorrentQuery | None = None
    ) -> TrTorrentList:
        client = self._ensure_client()
        torrent_ids = list(query.torrent_ids) if query and query.torrent_ids else None
        if query and query.fields:
            warnings.warn(
                "TorrentQuery.fields is deprecated; use get_torrents() for summaries, "
                "get_torrent_info() for detail, or hydrate_*() for heavy fields.",
                DeprecationWarning,
                stacklevel=2,
            )
        temp_torrents = client.get_torrents(ids=torrent_ids, arguments=list(_SUMMARY_FIELDS))

        if status is not None:
            target_status = convert_status(status, DownloaderKind.TRANSMISSION)
            temp_torrents = [
                torrent
                for torrent in temp_torrents
                if convert_status(
                    optional_adapter_field(torrent, "status", "unknown"),
                    DownloaderKind.TRANSMISSION,
                )
                == target_status
            ]

        return TrTorrentList(raw=temp_torrents)

    def get_torrents_snapshot(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> list[TorrentSnapshot]:
        client = self._ensure_client()
        torrent_ids = list(query.torrent_ids) if query and query.torrent_ids else None
        temp_torrents = client.get_torrents(ids=torrent_ids, arguments=list(_SNAPSHOT_FIELDS))

        if status is not None:
            target_status = convert_status(status, DownloaderKind.TRANSMISSION)
            temp_torrents = [
                torrent
                for torrent in temp_torrents
                if convert_status(
                    optional_adapter_field(torrent, "status", "unknown"),
                    DownloaderKind.TRANSMISSION,
                )
                == target_status
            ]

        snapshots: list[TorrentSnapshot] = []
        for torrent_data in temp_torrents:
            labels = _normalize_snapshot_labels(
                list(optional_adapter_field(torrent_data, "labels", []) or [])
            )
            status_value = convert_status(
                optional_adapter_field(torrent_data, "status", "unknown"),
                DownloaderKind.TRANSMISSION,
            )
            snapshots.append(
                TorrentSnapshot(
                    id=require_adapter_field(torrent_data, "id", context="Transmission snapshot"),
                    hash_string=optional_adapter_field(torrent_data, "hashString", ""),
                    name=require_adapter_field(
                        torrent_data,
                        "name",
                        context="Transmission snapshot",
                    ),
                    download_dir=optional_adapter_field(torrent_data, "downloadDir", ""),
                    progress=float(optional_adapter_field(torrent_data, "percentDone", 0) or 0),
                    size=int(optional_adapter_field(torrent_data, "totalSize", 0) or 0),
                    selected_size=int(optional_adapter_field(torrent_data, "sizeWhenDone", 0) or 0),
                    completed_size=int(optional_adapter_field(torrent_data, "haveValid", 0) or 0),
                    labels=labels,
                    status=status_value.value,
                    added_on=optional_adapter_field(torrent_data, "addedDate", None),
                )
            )

        return snapshots

    def get_torrents_lazy(
        self,
        arguments: list[str] | None = None,
        batch_size: int = 200,
        promote_thresholds: Optional[dict[str, int]] = None,
    ) -> TrLazyTorrentList:
        warnings.warn(
            "get_torrents_lazy() is deprecated; use explicit get_torrents(), "
            "get_torrent_info(), and hydrate_*() APIs.",
            DeprecationWarning,
            stacklevel=2,
        )

        client = self._ensure_client()
        strict_mode = arguments is not None
        requested_arguments = (
            list(arguments) if strict_mode else list(_DEFAULT_EAGER_SCALAR_ARGUMENTS)
        )
        if "id" not in requested_arguments:
            requested_arguments.append("id")

        temp_torrents = client.get_torrents(arguments=requested_arguments)
        torrent_ids = [
            int(require_adapter_field(torrent, "id", context="Transmission lazy torrent"))
            for torrent in temp_torrents
        ]
        resolver = TrLazyFieldResolver(
            client=client,
            torrent_ids=torrent_ids,
            strict_mode=strict_mode,
            batch_size=batch_size,
            promote_thresholds=promote_thresholds,
        )
        for torrent_data in temp_torrents:
            resolver.seed_from_torrent(torrent_data)

        return TrLazyTorrentList(
            raw=[TrLazyTorrentInfo(resolver, torrent_id) for torrent_id in torrent_ids]
        )

    def move_torrent(
        self, torrent: TorrentInfo, download_dir: str, move_files: bool = True
    ) -> bool:
        client = self._ensure_client()
        try:
            client.move_torrent_data(torrent.id, location=download_dir, move=move_files, timeout=20)
            current_torrent = client.get_torrent(
                torrent.id,
                arguments=["id", "name", "downloadDir"],
            )
            current_dir = optional_adapter_field(current_torrent, "downloadDir", "")
            if current_dir == download_dir.rstrip("/").rstrip("\\"):
                logger.info("move torrent %s to %s successfully", torrent.name, download_dir)
                return True

            logger.error(
                "move torrent %s to %s failed. current dir is %s",
                torrent.name,
                download_dir,
                current_dir,
            )
            return False
        except (
            TransportAuthenticationError,
            TransportConnectionError,
            TransportProtocolError,
        ) as exc:
            logger.error("move torrent failed: %s", exc)
            return False

    def get_torrent_info(self, torrent_id: int | str) -> Optional[TorrentInfo]:
        torrent = self._ensure_client().get_torrent(torrent_id, arguments=list(_DETAIL_FIELDS))
        if torrent is None:
            return None
        return TrTorrentList(raw=[torrent]).details[0]

    def set_labels(self, torrent: TorrentInfo, labels: list[str]) -> None:
        self._ensure_client().change_torrent(torrent.id, labels=labels)

    def set_labels_many(self, updates: Sequence[tuple[TorrentInfo, list[str]]]) -> None:
        client = self._ensure_client()
        buckets: dict[tuple[str, ...], list[int | str]] = {}

        for torrent, labels in updates:
            target_labels = tuple(_normalize_target_labels(labels))
            current_labels = tuple(_normalize_target_labels(list(torrent.labels or [])))
            if target_labels == current_labels:
                continue
            buckets.setdefault(target_labels, []).append(torrent.id)

        for target_labels, torrent_ids in buckets.items():
            client.change_torrent(torrent_ids, labels=list(target_labels))

    def hydrate_files(self, torrent_ids: TorrentIdInput) -> TorrentList:
        torrents = self._ensure_client().get_torrents(
            ids=self._normalize_torrent_ids(torrent_ids),
            arguments=list(_HYDRATE_FILE_FIELDS),
        )
        return TrTorrentList(raw=torrents)

    def hydrate_trackers(self, torrent_ids: TorrentIdInput) -> TorrentList:
        torrents = self._ensure_client().get_torrents(
            ids=self._normalize_torrent_ids(torrent_ids),
            arguments=list(_HYDRATE_TRACKER_FIELDS),
        )
        return TrTorrentList(raw=torrents)

    def set_category(self, torrent_id: int | str, category: str) -> None:
        torrent = self.get_torrent_info(torrent_id)
        if torrent is None:
            return
        self.set_labels(torrent, [category])

    def recheck_torrent(self, torrent_id: TorrentIdInput) -> None:
        self._ensure_client().verify_torrent(self._normalize_torrent_ids(torrent_id))

    def get_peer_info(self, torrent_id: int | str) -> TorrentPeerList:
        torrent = self._ensure_client().get_torrent(torrent_id, arguments=["id", "peers"])
        peers = list(optional_adapter_field(torrent, "peers", []) or [])
        return TrTorrentPeerList(raw=peers)

    def resume_torrent(self, torrent_id: TorrentIdInput) -> None:
        self._ensure_client().start_torrent(self._normalize_torrent_ids(torrent_id))

    def pause_torrent(self, torrent_id: TorrentIdInput) -> None:
        self._ensure_client().stop_torrent(self._normalize_torrent_ids(torrent_id))

    def reannounce_torrent(self, torrent_id: TorrentIdInput) -> None:
        self._ensure_client().reannounce_torrent(self._normalize_torrent_ids(torrent_id))

    def set_torrent_limits(
        self,
        torrent_id: TorrentIdInput,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if download_limit is not None:
            kwargs["download_limit"] = max(download_limit, 0)
            kwargs["download_limited"] = download_limit >= 0
        if upload_limit is not None:
            kwargs["upload_limit"] = max(upload_limit, 0)
            kwargs["upload_limited"] = upload_limit >= 0
        if kwargs:
            self._ensure_client().change_torrent(self._normalize_torrent_ids(torrent_id), **kwargs)

    def move_queue(self, torrent_id: TorrentIdInput, direction: QueueDirection) -> None:
        client = self._ensure_client()
        queue_actions = {
            QueueDirection.TOP: client.queue_top,
            QueueDirection.BOTTOM: client.queue_bottom,
            QueueDirection.UP: client.queue_up,
            QueueDirection.DOWN: client.queue_down,
        }
        queue_actions[direction](self._normalize_torrent_ids(torrent_id))

    def set_files(
        self,
        torrent_id: TorrentId,
        file_ids: Sequence[int],
        wanted: bool | None = None,
        priority: int | None = None,
    ) -> None:
        if not file_ids:
            return

        normalized_file_ids = list(file_ids)
        kwargs: dict[str, Any] = {}

        if wanted is True:
            kwargs["files_wanted"] = normalized_file_ids
        elif wanted is False:
            kwargs["files_unwanted"] = normalized_file_ids

        if priority is not None:
            if priority > 0:
                kwargs["priority_high"] = normalized_file_ids
            elif priority < 0:
                kwargs["priority_low"] = normalized_file_ids
            else:
                kwargs["priority_normal"] = normalized_file_ids

        if not kwargs:
            raise ValueError("set_files requires wanted or priority")

        self._ensure_client().change_torrent(torrent_id, **kwargs)

    def list_trackers(self, torrent_id: TorrentId) -> TorrentTrackerList:
        torrent = self._ensure_client().get_torrent(torrent_id, arguments=["id", "trackerStats"])
        tracker_stats = list(optional_adapter_field(torrent, "trackerStats", []) or [])
        return TrTorrentTrackerList(raw=tracker_stats)

    def add_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self._ensure_client().change_torrent(torrent_id, tracker_add=list(tracker_urls))

    def remove_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        tracker_ids = self._tracker_ids_from_urls(torrent_id, tracker_urls)
        if tracker_ids:
            self._ensure_client().change_torrent(torrent_id, tracker_remove=tracker_ids)

    def replace_tracker(self, torrent_id: TorrentId, old_url: str, new_url: str) -> None:
        tracker_ids = self._tracker_ids_from_urls(torrent_id, [old_url])
        if not tracker_ids:
            raise ValueError(f"tracker not found: {old_url}")
        self._ensure_client().change_torrent(
            torrent_id, tracker_replace=[(tracker_ids[0], new_url)]
        )

    def rename_torrent(
        self,
        torrent_id: TorrentId,
        new_name: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> None:
        if old_path is not None and new_path is not None:
            self._ensure_client().rename_torrent_path(torrent_id, old_path, new_path)
            return

        if new_name is not None:
            raise NotImplementedError(
                "Transmission does not support renaming torrent display name via RPC."
            )

        raise ValueError("rename_torrent requires new_name or old_path/new_path")

    def set_global_limits(
        self,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if download_limit is not None:
            kwargs["speed_limit_down"] = max(download_limit, 0)
            kwargs["speed_limit_down_enabled"] = download_limit >= 0
        if upload_limit is not None:
            kwargs["speed_limit_up"] = max(upload_limit, 0)
            kwargs["speed_limit_up_enabled"] = upload_limit >= 0
        if kwargs:
            self._ensure_client().set_session(**kwargs)

    def get_client_stats(self) -> ClientStats:
        client = self._ensure_client()
        session = client.get_session()
        stats = client.session_stats()
        return ClientStats(
            download_speed=best_effort_adapter_field(
                stats,
                "downloadSpeed",
                0,
                logger=logger,
                context="Transmission client stats",
            ),
            upload_speed=best_effort_adapter_field(
                stats,
                "uploadSpeed",
                0,
                logger=logger,
                context="Transmission client stats",
            ),
            download_limit=best_effort_adapter_field(
                session,
                "speed_limit_down",
                0,
                logger=logger,
                context="Transmission client session",
            ),
            upload_limit=best_effort_adapter_field(
                session,
                "speed_limit_up",
                0,
                logger=logger,
                context="Transmission client session",
            ),
            download_limited=best_effort_adapter_field(
                session,
                "speed_limit_down_enabled",
                False,
                logger=logger,
                context="Transmission client session",
            ),
            upload_limited=best_effort_adapter_field(
                session,
                "speed_limit_up_enabled",
                False,
                logger=logger,
                context="Transmission client session",
            ),
        )

    def _tracker_ids_from_urls(
        self, torrent_id: TorrentId, tracker_urls: Sequence[str]
    ) -> list[int]:
        if not tracker_urls:
            return []
        torrent = self._ensure_client().get_torrent(torrent_id, arguments=["id", "trackerStats"])
        tracker_stats = list(optional_adapter_field(torrent, "trackerStats", []) or [])
        tracker_id_by_url = {
            tracker.get("announce", ""): int(tracker["id"])
            for tracker in tracker_stats
            if tracker.get("announce") and tracker.get("id") is not None
        }
        return [tracker_id_by_url[url] for url in tracker_urls if url in tracker_id_by_url]
