"""Domain status model and downloader-specific status adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol, Union


class TorrentStatus(str, Enum):
    """Domain-level torrent state independent from downloader vendors."""

    UNKNOWN = "unknown"
    ERROR = "error"
    STOPPED = "stopped"
    CHECKING = "checking"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    METADATA = "metadata"
    ALLOCATING = "allocating"
    SEEDING = "seeding"
    MOVING = "moving"


class DownloaderKind(str, Enum):
    """Supported downloader kinds for status mapping."""

    QBITTORRENT = "qbittorrent"
    TRANSMISSION = "transmission"


RawTorrentStatus = Union[int, str, TorrentStatus]


class StatusMapper(Protocol):
    """Strategy interface for raw-status to domain-status mapping."""

    def to_domain(self, raw_status: RawTorrentStatus) -> TorrentStatus:
        """Convert downloader raw status to domain status."""


@dataclass(frozen=True)
class DictStatusMapper:
    """Dictionary-backed status mapping strategy."""

    str_mapping: Mapping[str, TorrentStatus]
    int_mapping: Mapping[int, str] | None = None

    def to_domain(self, raw_status: RawTorrentStatus) -> TorrentStatus:
        if isinstance(raw_status, TorrentStatus):
            return raw_status

        status_key: str | None
        if isinstance(raw_status, int):
            status_key = None if self.int_mapping is None else self.int_mapping.get(raw_status)
        else:
            status_key = raw_status.strip()

        if status_key is None:
            return TorrentStatus.UNKNOWN
        return self.str_mapping.get(status_key, TorrentStatus.UNKNOWN)


_TRANSMISSION_INT_TO_STR = {
    0: "stopped",
    1: "check pending",
    2: "checking",
    3: "download pending",
    4: "downloading",
    5: "seed pending",
    6: "seeding",
}

_TRANSMISSION_STR_MAPPING: Mapping[str, TorrentStatus] = {
    "stopped": TorrentStatus.STOPPED,
    "check pending": TorrentStatus.CHECKING,
    "checking": TorrentStatus.CHECKING,
    "download pending": TorrentStatus.QUEUED,
    "downloading": TorrentStatus.DOWNLOADING,
    "seed pending": TorrentStatus.QUEUED,
    "seeding": TorrentStatus.SEEDING,
    "unknown": TorrentStatus.UNKNOWN,
}

_QBITTORRENT_STR_MAPPING: Mapping[str, TorrentStatus] = {
    "error": TorrentStatus.ERROR,
    "missingFiles": TorrentStatus.ERROR,
    "uploading": TorrentStatus.SEEDING,
    "pausedUP": TorrentStatus.STOPPED,
    "queuedUP": TorrentStatus.QUEUED,
    "stalledUP": TorrentStatus.SEEDING,
    "checkingUP": TorrentStatus.CHECKING,
    "forcedUP": TorrentStatus.SEEDING,
    "allocating": TorrentStatus.ALLOCATING,
    "downloading": TorrentStatus.DOWNLOADING,
    "metaDL": TorrentStatus.METADATA,
    "pausedDL": TorrentStatus.STOPPED,
    "queuedDL": TorrentStatus.QUEUED,
    "stalledDL": TorrentStatus.DOWNLOADING,
    "checkingDL": TorrentStatus.CHECKING,
    "forcedDL": TorrentStatus.DOWNLOADING,
    "checkingResumeData": TorrentStatus.CHECKING,
    "moving": TorrentStatus.MOVING,
    "unknown": TorrentStatus.UNKNOWN,
}

_STATUS_MAPPERS: Mapping[DownloaderKind, StatusMapper] = {
    DownloaderKind.QBITTORRENT: DictStatusMapper(str_mapping=_QBITTORRENT_STR_MAPPING),
    DownloaderKind.TRANSMISSION: DictStatusMapper(
        str_mapping=_TRANSMISSION_STR_MAPPING,
        int_mapping=_TRANSMISSION_INT_TO_STR,
    ),
}

_DOWNLOADING_STATES = frozenset(
    {
        TorrentStatus.DOWNLOADING,
        TorrentStatus.METADATA,
        TorrentStatus.ALLOCATING,
    }
)

_SEEDING_STATES = frozenset({TorrentStatus.SEEDING})
_STOPPED_STATES = frozenset({TorrentStatus.STOPPED})


def _normalize_downloader_kind(kind: DownloaderKind | str) -> DownloaderKind:
    if isinstance(kind, DownloaderKind):
        return kind
    try:
        return DownloaderKind(kind)
    except ValueError as exc:
        raise ValueError(f"Unsupported downloader kind: {kind}") from exc


def convert_status(status: RawTorrentStatus, kind: DownloaderKind | str) -> TorrentStatus:
    """Convert raw status from a specific downloader to domain status."""
    normalized_kind = _normalize_downloader_kind(kind)
    mapper = _STATUS_MAPPERS[normalized_kind]
    return mapper.to_domain(status)


def is_downloading(status: TorrentStatus) -> bool:
    """Whether domain status represents active downloading pipeline."""
    return status in _DOWNLOADING_STATES


def is_seeding(status: TorrentStatus) -> bool:
    """Whether domain status represents seeding."""
    return status in _SEEDING_STATES


def is_stopped(status: TorrentStatus) -> bool:
    """Whether domain status represents stopped/paused torrent."""
    return status in _STOPPED_STATES


def is_completed(progress: float | None) -> bool:
    """Completion should be decided by progress, not by downloader state name."""
    return progress is not None and progress >= 1.0
