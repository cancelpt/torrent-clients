"""Shared base interfaces and helpers for torrent clients."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence, TypeVar, Union, cast, runtime_checkable

from torrent_clients.torrent.torrent_info import TorrentInfo, TorrentList
from torrent_clients.torrent.torrent_peer import TorrentPeerList
from torrent_clients.torrent.torrent_tracker import TorrentTrackerList

CapabilityT = TypeVar("CapabilityT")
TorrentId = Union[int, str]
TorrentIdInput = Union[TorrentId, Sequence[TorrentId]]
_MISSING_FIELD = object()


class MissingAdapterFieldError(RuntimeError):
    """Raised when a downloader payload omits a field the adapter requires."""


def adapter_field_value(container: Any, key: str, default: Any = _MISSING_FIELD) -> Any:
    """Fetch a raw downloader field from mapping-like or attribute-style payloads."""
    value = _MISSING_FIELD
    if isinstance(container, Mapping):
        value = container.get(key, _MISSING_FIELD)
    else:
        getter = getattr(container, "get", None)
        if callable(getter):
            value = getter(key, _MISSING_FIELD)

    if value is not _MISSING_FIELD:
        return value

    value = getattr(container, key, _MISSING_FIELD)
    if value is _MISSING_FIELD:
        return default
    return value


def require_adapter_field(container: Any, key: str, *, context: str) -> Any:
    """Return a required downloader field or raise a descriptive adapter error."""
    value = adapter_field_value(container, key, _MISSING_FIELD)
    if value is _MISSING_FIELD:
        raise MissingAdapterFieldError(f"{context} missing required field '{key}'")
    return value


def optional_adapter_field(container: Any, key: str, default: Any) -> Any:
    """Return an optional downloader field while preserving explicit falsy values."""
    value = adapter_field_value(container, key, _MISSING_FIELD)
    if value is _MISSING_FIELD:
        return default
    return value


def best_effort_adapter_field(
    container: Any,
    key: str,
    default: Any,
    *,
    logger: Any,
    context: str,
) -> Any:
    """Return a best-effort field and log when a fallback is used for a missing key."""
    value = adapter_field_value(container, key, _MISSING_FIELD)
    if value is _MISSING_FIELD:
        logger.warning("%s missing expected field '%s'; using fallback %r", context, key, default)
        return default
    return value


@dataclass(frozen=True, eq=False)
class ClientStats(Mapping[str, Any]):
    """Structured client/session statistics with mapping-style compatibility."""

    download_speed: int
    upload_speed: int
    download_limit: int
    upload_limit: int
    download_limited: bool | None = None
    upload_limited: bool | None = None

    def _mapping(self) -> dict[str, Any]:
        values = {
            "download_speed": self.download_speed,
            "upload_speed": self.upload_speed,
            "download_limit": self.download_limit,
            "upload_limit": self.upload_limit,
        }
        if self.download_limited is not None:
            values["download_limited"] = self.download_limited
        if self.upload_limited is not None:
            values["upload_limited"] = self.upload_limited
        return values

    def __getitem__(self, key: str) -> Any:
        return self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping())

    def __len__(self) -> int:
        return len(self._mapping())

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping().get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return self._mapping()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ClientStats):
            return (
                self.download_speed == other.download_speed
                and self.upload_speed == other.upload_speed
                and self.download_limit == other.download_limit
                and self.upload_limit == other.upload_limit
                and self.download_limited == other.download_limited
                and self.upload_limited == other.upload_limited
            )
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other.items())
        return NotImplemented

    def __hash__(self) -> int:
        return hash(
            (
                self.download_speed,
                self.upload_speed,
                self.download_limit,
                self.upload_limit,
                self.download_limited,
                self.upload_limited,
            )
        )


@dataclass(frozen=True)
class TorrentQuery:
    """Unified query object for torrent list retrieval across downloaders."""

    category: str | None = None
    tag: str | None = None
    sort: str | None = None
    reverse: bool | None = None
    limit: int | None = None
    offset: int | None = None
    torrent_ids: Sequence[TorrentId] | None = None
    fields: Sequence[str] | None = None


@dataclass(frozen=True)
class TorrentSnapshot:
    """Lightweight torrent state snapshot for cache-driven workflows."""

    id: TorrentId
    hash_string: str
    name: str
    download_dir: str
    progress: float
    size: int
    selected_size: int
    completed_size: int
    labels: list[str]
    status: str
    added_on: int | None = None


class UnsupportedClientCapabilityError(RuntimeError):
    """Raised when accessing a downloader-specific capability not supported by a client."""


class QueueDirection(str, Enum):
    """Supported queue movement directions."""

    TOP = "top"
    BOTTOM = "bottom"
    UP = "up"
    DOWN = "down"


@runtime_checkable
class SupportsIpBan(Protocol):
    """Capability protocol for banning peers by IP."""

    def ban_ips(self, ips: Sequence[str]) -> None:
        """Ban peer IP addresses in downloader-specific implementations."""


@runtime_checkable
class SupportsLazyTorrentFetch(Protocol):
    """Capability protocol for lazy/hybrid torrent fetching."""

    def get_torrents_lazy(
        self,
        arguments: list[str] | None = None,
        batch_size: int = 200,
        promote_thresholds: dict[str, int] | None = None,
    ) -> Sequence[Any]:
        """Fetch torrents through lazy/hybrid strategy."""


class BaseClient:
    """Base class for downloaders."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        dl_type: str | None = None,
        name: str | None = None,
    ) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.dl_type = dl_type
        self.name = name

    @staticmethod
    def _is_remote_torrent_input(torrent_input: str) -> bool:
        return torrent_input.startswith("magnet:") or torrent_input.startswith("http")

    def _prepare_torrent_input(self, torrent_input: str) -> tuple[str, str | bytes]:
        """
        Normalize torrent input for client APIs.

        Returns:
            ("url", raw_input) for magnet/http URL,
            ("file", bytes_payload) for local torrent file.
        """
        if self._is_remote_torrent_input(torrent_input):
            return "url", torrent_input

        torrent_path = Path(torrent_input)
        if not torrent_path.exists():
            raise FileNotFoundError(f"torrent file does not exist: {torrent_input}")

        payload = torrent_path.read_bytes()
        if not payload:
            raise ValueError("torrent file is empty")

        return "file", payload

    @staticmethod
    def _normalize_torrent_ids(torrent_ids: TorrentIdInput) -> list[TorrentId]:
        """Normalize single/multi torrent id input to a list."""
        if isinstance(torrent_ids, (int, str)):
            return [torrent_ids]
        return list(torrent_ids)

    def supports_capability(self, capability: type[CapabilityT]) -> bool:
        """Whether this client supports a downloader-specific capability protocol."""
        return isinstance(self, capability)

    def require_capability(self, capability: type[CapabilityT]) -> CapabilityT:
        """Return self as a capability interface or raise when unsupported."""
        if not self.supports_capability(capability):
            capability_name = getattr(capability, "__name__", str(capability))
            raise UnsupportedClientCapabilityError(
                f"{self.__class__.__name__} does not support capability {capability_name}"
            )
        return cast(CapabilityT, self)

    def login(self) -> Any:
        """Login to the downloader."""
        raise NotImplementedError("Subclasses must implement this method.")

    def add_torrent(
        self,
        torrent_url: str,
        upload_limit: int | None = None,
        download_limit: int | None = None,
        download_dir: str | None = None,
        is_paused: bool = False,
        skip_checking: bool = False,
    ) -> bool:
        """Add a torrent to the download queue."""
        raise NotImplementedError("Subclasses must implement this method.")

    def remove_torrent(
        self,
        torrent_id: TorrentIdInput,
        delete_data: bool = False,
    ) -> None:
        """Remove a torrent from the download queue."""
        raise NotImplementedError("Subclasses must implement this method.")

    def get_torrents(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> TorrentList:
        """Get the list of torrents in the download queue."""
        raise NotImplementedError("Subclasses must implement this method.")

    def get_torrents_snapshot(
        self,
        status: str | None = None,
        query: TorrentQuery | None = None,
    ) -> list[TorrentSnapshot]:
        """Get a lightweight list of torrents for snapshot-based workflows.

        Implementations must at least honor `status` and `query.torrent_ids`.
        Other query fields may be ignored to preserve lightweight semantics.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def move_torrent(
        self,
        torrent: TorrentInfo,
        download_dir: str,
        move_files: bool = True,
    ) -> bool:
        """Move a torrent to a different download directory."""
        raise NotImplementedError("Subclasses must implement this method.")

    def get_torrent_info(self, torrent_id: int | str) -> Optional[TorrentInfo]:
        """Get the information of a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_labels(self, torrent: TorrentInfo, labels: list[str]) -> None:
        """Set labels for a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_category(self, torrent_id: int | str, category: str) -> None:
        """Set category for a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def recheck_torrent(self, torrent_id: TorrentIdInput) -> None:
        """Recheck a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def resume_torrent(self, torrent_id: TorrentIdInput) -> None:
        """Resume a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def pause_torrent(self, torrent_id: TorrentIdInput) -> None:
        """Pause a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def get_peer_info(self, torrent_id: int | str) -> TorrentPeerList:
        """Get peer information for a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def reannounce_torrent(self, torrent_id: TorrentIdInput) -> None:
        """Manually reannounce one or more torrents."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_torrent_limits(
        self,
        torrent_id: TorrentIdInput,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        """Set per-torrent speed limits."""
        raise NotImplementedError("Subclasses must implement this method.")

    def move_queue(self, torrent_id: TorrentIdInput, direction: QueueDirection) -> None:
        """Move torrents within the queue."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_files(
        self,
        torrent_id: TorrentId,
        file_ids: Sequence[int],
        wanted: bool | None = None,
        priority: int | None = None,
    ) -> None:
        """Update file wanted-state or file priority for a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def hydrate_files(self, torrent_ids: TorrentIdInput) -> TorrentList:
        """Hydrate torrent file lists for a batch of torrents."""
        raise NotImplementedError("Subclasses must implement this method.")

    def hydrate_trackers(self, torrent_ids: TorrentIdInput) -> TorrentList:
        """Hydrate torrent tracker lists for a batch of torrents."""
        raise NotImplementedError("Subclasses must implement this method.")

    def list_trackers(self, torrent_id: TorrentId) -> TorrentTrackerList:
        """List trackers for a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def add_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        """Add trackers to a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def remove_trackers(self, torrent_id: TorrentId, tracker_urls: Sequence[str]) -> None:
        """Remove trackers from a torrent."""
        raise NotImplementedError("Subclasses must implement this method.")

    def replace_tracker(self, torrent_id: TorrentId, old_url: str, new_url: str) -> None:
        """Replace a tracker URL."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_labels_many(self, updates: Sequence[tuple[TorrentInfo, list[str]]]) -> None:
        """Set labels for many torrents, falling back to per-torrent updates."""
        for torrent, labels in updates:
            self.set_labels(torrent, labels)

    def rename_torrent(
        self,
        torrent_id: TorrentId,
        new_name: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> None:
        """Rename torrent name or a path within torrent content."""
        raise NotImplementedError("Subclasses must implement this method.")

    def set_global_limits(
        self,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> None:
        """Set client-wide speed limits."""
        raise NotImplementedError("Subclasses must implement this method.")

    def get_client_stats(self) -> ClientStats:
        """Get client/session statistics."""
        raise NotImplementedError("Subclasses must implement this method.")
