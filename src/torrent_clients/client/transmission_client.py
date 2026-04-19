from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any, Dict, List, Literal, Optional, cast
from urllib.parse import urlparse

from transmission_rpc import Client, Torrent, error

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
from torrent_clients.torrent.torrent_status import DownloaderKind, TorrentStatus, convert_status
from torrent_clients.torrent.torrent_tracker import TorrentTracker, TorrentTrackerList

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


def _normalize_snapshot_labels(labels: list[str]) -> list[str]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    return sorted(set(cleaned))


def _normalize_target_labels(labels: list[str]) -> list[str]:
    cleaned = [str(label).strip() for label in labels if str(label).strip()]
    return sorted(set(cleaned))


class MissingTorrentFieldError(RuntimeError):
    """Raised when strict arguments mode accesses a field that was not requested."""


def find_invalid_characters(s: str) -> list[str]:
    # 返回所有不合法的字符
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
        # 如果文件名包含非法字符
        if find_invalid_characters(file_name):
            logger.debug("种子%s，文件名包含非法字符：%s", self.torrent_id, file_name)

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
        # 提取 'announce' 字段
        announce_url = optional_adapter_field(tracker_data, "announce", "")

        # 验证URL
        if self.valid_url_pattern.match(announce_url):
            # 使用常量和字典的直接访问
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
    def transform(self, torrent_data: Torrent) -> TorrentInfo:
        torrent_id = require_adapter_field(torrent_data, "id", context="Transmission torrent")
        name = require_adapter_field(torrent_data, "name", context="Transmission torrent")

        # 如果文件名包含非法字符
        if find_invalid_characters(name):
            logger.warning("种子%s，名称包含非法字符：%s", torrent_id, name)
        file_data = []
        if "files" in torrent_data.fields:
            file_data.append(torrent_data.get("files", []))
            if "fileStats" in torrent_data.fields:
                file_data.append(torrent_data.get("fileStats", []))

        files = TrTorrentFileList(torrent_id, raw=file_data)
        if "trackerStats" in torrent_data.fields:
            trackers = TrTorrentTrackerList(raw=torrent_data.tracker_stats)
        else:
            trackers = TrTorrentTrackerList(raw=[])

        labels = optional_adapter_field(torrent_data, "labels", [])
        if len(labels) > 0:
            category = labels[0]
        else:
            category = ""

        # 转换状态
        origin_status = optional_adapter_field(torrent_data, "status", "unknown")

        status = convert_status(origin_status, DownloaderKind.TRANSMISSION)

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
            comment=optional_adapter_field(torrent_data, "comment", ""),
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
        client: Client,
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

    def seed_from_torrent(self, torrent_data: Torrent) -> None:
        torrent_id = int(
            require_adapter_field(torrent_data, "id", context="Transmission lazy torrent")
        )

        loaded = self._loaded_fields.setdefault(torrent_id, set())
        values = self._values.setdefault(torrent_id, {})
        fields = set(getattr(torrent_data, "fields", set()))

        for field in fields:
            values[field] = torrent_data.get(field)
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
            try:
                torrents = self._client.get_torrents(ids=batch_ids, arguments=requested_fields)
            except TypeError:
                torrents = [
                    self._client.get_torrent(torrent_id, arguments=requested_fields)
                    for torrent_id in batch_ids
                ]
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
        self.client = None
        self.dl_type = "transmission"

    def login(self) -> bool:
        parsed_url = urlparse(self.url)
        host = parsed_url.hostname
        scheme = parsed_url.scheme
        if scheme not in ["http", "https"]:
            raise ValueError(f"Unsupported protocol: {scheme}")
        scheme_literal = cast(Literal["http", "https"], scheme)
        port = parsed_url.port or (80 if scheme == "http" else 443)
        try:
            self.client = Client(
                protocol=scheme_literal,
                host=host,
                port=port,
                username=self.username,
                password=self.password,
            )
            logger.info("Successfully connected to Transmission: %s", self.url)
        except error.TransmissionError as e:
            logger.error("Failed to connect to Transmission: %s, %s", self.url, e)
            return False

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Unknown error: %s, please check the URL %s", e, self.url)
            return False

        return True

    def add_torrent(
        self,
        torrent_url: str,
        upload_limit: int | None = None,
        download_limit: int | None = None,
        download_dir: str | None = None,
        is_paused: bool = True,
        skip_checking: bool = False,
    ) -> bool:

        torrent_input = torrent_url

        try:
            _, torrent_input = self._prepare_torrent_input(torrent_input)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("%s", exc)
            return False

        try:
            # 先加种子，暂停
            result = self.client.add_torrent(torrent_input, download_dir=download_dir, paused=True)
        except error.TransmissionError as e:
            logger.error("添加种子失败：%s", e)
            return False

        if result:
            if upload_limit is None:
                upload_limit = -1
            if download_limit is None:
                download_limit = -1
            # 重复确认限速是否生效，最大尝试次数为3
            retry = 3
            while retry > 0:
                self.client.change_torrent(
                    result.id,
                    upload_limit=upload_limit,
                    download_limit=download_limit,
                    upload_limited=(upload_limit != -1),
                    download_limited=(download_limit != -1),
                )
                torrent = self.client.get_torrent(result.id)
                if (
                    (upload_limit != -1 and torrent.upload_limit == upload_limit)
                    or upload_limit == -1
                ) and (
                    (download_limit != -1 and torrent.download_limit == download_limit)
                    or download_limit == -1
                ):
                    break
                retry -= 1
            if retry == 0:
                logger.error("Cannnot set upload/download limit for torrent: %s", result.id)
                return False
            # 如果跳过检查
            if skip_checking:
                # 重新汇报种子（tr3跳校验）
                self.client.reannounce_torrent(result.id)
            # 开始下载
            if not is_paused:
                self.client.start_torrent(result.id)
        else:
            logger.error("Add torrent failed: %s", torrent_input)
            return False
        return True

    def remove_torrent(self, torrent_id: TorrentIdInput, delete_data: bool = False) -> None:
        torrent_ids = self._normalize_torrent_ids(torrent_id)
        self.client.remove_torrent(torrent_ids, delete_data=delete_data)

    def get_torrents(
        self, status: str | None = None, query: TorrentQuery | None = None
    ) -> TrTorrentList:
        if self.client is None:
            self.login()

        # 如果还是None，说明登录失败
        if self.client is None:
            raise ValueError("Cannot login to Transmission.")

        torrent_ids = list(query.torrent_ids) if query and query.torrent_ids else None
        arguments = list(query.fields) if query and query.fields else None

        temp_torrents = self.client.get_torrents(ids=torrent_ids, arguments=arguments)

        if status is not None:
            target_status = convert_status(status, DownloaderKind.TRANSMISSION)
            temp_torrents = [
                torrent
                for torrent in temp_torrents
                if convert_status(torrent.get("status", "unknown"), DownloaderKind.TRANSMISSION)
                == target_status
            ]

        return TrTorrentList(raw=temp_torrents)

    def get_torrents_snapshot(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> list[TorrentSnapshot]:
        if self.client is None:
            self.login()

        if self.client is None:
            raise ValueError("Cannot login to Transmission.")

        torrent_ids = list(query.torrent_ids) if query and query.torrent_ids else None
        arguments = list(_SNAPSHOT_FIELDS)

        temp_torrents = self.client.get_torrents(ids=torrent_ids, arguments=arguments)

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
            status = convert_status(
                optional_adapter_field(torrent_data, "status", "unknown"),
                DownloaderKind.TRANSMISSION,
            )
            snapshots.append(
                TorrentSnapshot(
                    id=require_adapter_field(
                        torrent_data,
                        "id",
                        context="Transmission snapshot",
                    ),
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
                    status=status.value,
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
        if self.client is None:
            self.login()

        if self.client is None:
            raise ValueError("Cannot login to Transmission.")

        strict_mode = arguments is not None
        requested_arguments = (
            list(arguments) if strict_mode else list(_DEFAULT_EAGER_SCALAR_ARGUMENTS)
        )
        if "id" not in requested_arguments:
            requested_arguments.append("id")

        temp_torrents = self.client.get_torrents(arguments=requested_arguments)
        torrent_ids = [
            int(require_adapter_field(torrent, "id", context="Transmission lazy torrent"))
            for torrent in temp_torrents
        ]
        resolver = TrLazyFieldResolver(
            client=self.client,
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
        try:
            self.client.move_torrent_data(
                torrent.id, location=download_dir, move=move_files, timeout=20
            )
            # 获取当前种子信息
            torrent = self.client.get_torrent(torrent.id)
            # strip download_dir，忽略尾部的斜杠
            # 判断是否移动成功
            if torrent.download_dir == download_dir.rstrip("/").rstrip("\\"):
                logger.info("Move torrent %s to %s successfully.", torrent.name, download_dir)
                return True

            logger.error(
                "Move torrent %s to %s failed. Now the download dir is %s",
                torrent.name,
                download_dir,
                torrent.download_dir,
            )
            return False

        except error.TransmissionError as e:
            logger.error("Move torrent failed: %s", e)
            return False

    def get_torrent_info(self, torrent_id: int | str) -> Optional[TorrentInfo]:
        torrent = self.client.get_torrent(torrent_id)
        if torrent is None:
            return None
        return TrTorrentList(raw=[torrent]).details[0]

    def set_labels(self, torrent: TorrentInfo, labels: list[str]) -> None:
        self.client.change_torrent(torrent.id, labels=labels)

    def set_labels_many(self, updates: Sequence[tuple[TorrentInfo, list[str]]]) -> None:
        buckets: dict[tuple[str, ...], list[int | str]] = {}

        for torrent, labels in updates:
            target_labels = tuple(_normalize_target_labels(labels))
            current_labels = tuple(_normalize_target_labels(list(torrent.labels or [])))
            if target_labels == current_labels:
                continue
            buckets.setdefault(target_labels, []).append(torrent.id)

        for target_labels, torrent_ids in buckets.items():
            self.client.change_torrent(torrent_ids, labels=list(target_labels))

    def hydrate_files(self, torrent_ids: TorrentIdInput) -> TorrentList:
        return self.get_torrents(
            query=TorrentQuery(
                torrent_ids=self._normalize_torrent_ids(torrent_ids),
                fields=[
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
                ],
            )
        )

    def hydrate_trackers(self, torrent_ids: TorrentIdInput) -> TorrentList:
        return self.get_torrents(
            query=TorrentQuery(
                torrent_ids=self._normalize_torrent_ids(torrent_ids),
                fields=["id", "hashString", "name", "downloadDir", "trackerStats"],
            )
        )

    def set_category(self, torrent_id: int | str, category: str) -> None:
        torrent = self.get_torrent_info(torrent_id)
        if torrent is None:
            return
        self.set_labels(torrent, [category])

    def recheck_torrent(self, torrent_id: TorrentIdInput) -> None:
        self.client.verify_torrent(self._normalize_torrent_ids(torrent_id))

    def get_peer_info(self, torrent_id: int | str) -> TorrentPeerList:
        torrent = self.client.get_torrent(torrent_id, arguments=["id", "peers"])
        peers = torrent.get("peers", [])
        return TrTorrentPeerList(raw=peers)

    def resume_torrent(self, torrent_id: TorrentIdInput) -> None:
        self.client.start_torrent(self._normalize_torrent_ids(torrent_id))

    def pause_torrent(self, torrent_id: TorrentIdInput) -> None:
        self.client.stop_torrent(self._normalize_torrent_ids(torrent_id))

    def reannounce_torrent(self, torrent_id: TorrentIdInput) -> None:
        self.client.reannounce_torrent(self._normalize_torrent_ids(torrent_id))

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
            self.client.change_torrent(self._normalize_torrent_ids(torrent_id), **kwargs)

    def move_queue(self, torrent_id: TorrentIdInput, direction: QueueDirection) -> None:
        queue_actions = {
            QueueDirection.TOP: self.client.queue_top,
            QueueDirection.BOTTOM: self.client.queue_bottom,
            QueueDirection.UP: self.client.queue_up,
            QueueDirection.DOWN: self.client.queue_down,
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

        self.client.change_torrent(torrent_id, **kwargs)

    def list_trackers(self, torrent_id: TorrentId) -> TorrentTrackerList:
        torrent = self.client.get_torrent(torrent_id, arguments=["id", "trackerStats"])
        tracker_stats = torrent.get("trackerStats", getattr(torrent, "tracker_stats", []))
        return TrTorrentTrackerList(raw=tracker_stats)

    def add_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        if not tracker_urls:
            return
        self.client.change_torrent(torrent_id, tracker_add=list(tracker_urls))

    def remove_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        tracker_ids = self._tracker_ids_from_urls(torrent_id, tracker_urls)
        if tracker_ids:
            self.client.change_torrent(torrent_id, tracker_remove=tracker_ids)

    def replace_tracker(self, torrent_id: TorrentId, old_url: str, new_url: str) -> None:
        tracker_ids = self._tracker_ids_from_urls(torrent_id, [old_url])
        if not tracker_ids:
            raise ValueError(f"tracker not found: {old_url}")
        self.client.change_torrent(torrent_id, tracker_replace=[(tracker_ids[0], new_url)])

    def rename_torrent(
        self,
        torrent_id: TorrentId,
        new_name: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> None:
        if old_path is not None and new_path is not None:
            self.client.rename_torrent_path(torrent_id, old_path, new_path)
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
            self.client.set_session(**kwargs)

    def get_client_stats(self) -> ClientStats:
        session = self.client.get_session()
        stats = self.client.session_stats()
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
        torrent = self.client.get_torrent(torrent_id, arguments=["id", "trackerStats"])
        tracker_stats = torrent.get("trackerStats", getattr(torrent, "tracker_stats", []))
        tracker_id_by_url = {
            tracker.get("announce", ""): int(tracker["id"])
            for tracker in tracker_stats
            if tracker.get("announce") and tracker.get("id") is not None
        }
        return [tracker_id_by_url[url] for url in tracker_urls if url in tracker_id_by_url]
