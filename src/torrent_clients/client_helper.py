"""Downloader factory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Union

from torrent_clients.client.base_client import TorrentSnapshot
from torrent_clients.client.client_type import ClientType
from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient

SupportedClient = Union[QbittorrentClient, TransmissionClient]


def _normalize_client_type(dl_type: ClientType | str) -> ClientType:
    if isinstance(dl_type, ClientType):
        return dl_type

    try:
        return ClientType(dl_type)
    except ValueError as exc:
        raise ValueError(f"Invalid client type: {dl_type}") from exc


def get_downloader_client(
    url: str,
    username: str | None,
    password: str | None,
    dl_type: ClientType | str,
    name: str,
) -> SupportedClient:
    normalized = _normalize_client_type(dl_type)

    if normalized is ClientType.QBITTORRENT:
        return QbittorrentClient(url, username, password, name=name)
    if normalized is ClientType.TRANSMISSION:
        return TransmissionClient(url, username, password, name=name)

    raise ValueError(f"Invalid client type: {dl_type}")


@dataclass(frozen=True)
class FetchResult:
    """Aggregated torrent fetch output across multiple downloaders."""

    torrents: list[Any]
    torrent_id_to_client: dict[str, SupportedClient]
    failed_downloaders: list[str]


@dataclass(frozen=True)
class SnapshotFetchResult:
    """Aggregated torrent snapshot fetch output across multiple downloaders."""

    snapshots: list[TorrentSnapshot]
    snapshot_id_to_client: dict[str, SupportedClient]
    failed_downloaders: list[str]


def fetch_torrents(
    downloaders: Iterable[Any],
    *,
    include_disabled: bool = False,
    client_factory: Callable[..., SupportedClient] = get_downloader_client,
) -> FetchResult:
    """
    Fetch torrents from multiple downloaders with partial-failure tolerance.

    Returns all fetched torrents, a hash-to-client mapping, and failed downloader names.
    """
    all_torrents: list[Any] = []
    torrent_id_to_client: dict[str, SupportedClient] = {}
    failed_downloaders: list[str] = []

    for downloader in downloaders:
        is_enabled = bool(getattr(downloader, "enabled", True))
        if not include_disabled and not is_enabled:
            continue

        downloader_name = str(getattr(downloader, "name", "unknown"))
        try:
            downloader_client = client_factory(
                getattr(downloader, "url", ""),
                getattr(downloader, "username", None),
                getattr(downloader, "password", None),
                getattr(downloader, "dl_type", ""),
                downloader_name,
            )
            torrents = downloader_client.get_torrents()
        except Exception:  # pylint: disable=broad-exception-caught
            failed_downloaders.append(downloader_name)
            continue

        all_torrents.extend(torrents)
        for torrent in torrents:
            torrent_hash = getattr(torrent, "hash_string", "")
            if torrent_hash:
                torrent_id_to_client[str(torrent_hash)] = downloader_client

    return FetchResult(
        torrents=all_torrents,
        torrent_id_to_client=torrent_id_to_client,
        failed_downloaders=failed_downloaders,
    )


def fetch_torrent_snapshots(
    downloaders: Iterable[Any],
    *,
    include_disabled: bool = False,
    client_factory: Callable[..., SupportedClient] = get_downloader_client,
) -> SnapshotFetchResult:
    """
    Fetch torrent snapshots from multiple downloaders with partial-failure tolerance.

    Returns all fetched snapshots, a hash-to-client mapping, and failed downloader names.
    """
    all_snapshots: list[TorrentSnapshot] = []
    snapshot_id_to_client: dict[str, SupportedClient] = {}
    failed_downloaders: list[str] = []

    for downloader in downloaders:
        is_enabled = bool(getattr(downloader, "enabled", True))
        if not include_disabled and not is_enabled:
            continue

        downloader_name = str(getattr(downloader, "name", "unknown"))
        try:
            downloader_client = client_factory(
                getattr(downloader, "url", ""),
                getattr(downloader, "username", None),
                getattr(downloader, "password", None),
                getattr(downloader, "dl_type", ""),
                downloader_name,
            )
            snapshots = downloader_client.get_torrents_snapshot()
        except Exception:  # pylint: disable=broad-exception-caught
            failed_downloaders.append(downloader_name)
            continue

        all_snapshots.extend(snapshots)
        for snapshot in snapshots:
            snapshot_hash = getattr(snapshot, "hash_string", "")
            if snapshot_hash:
                snapshot_id_to_client[str(snapshot_hash)] = downloader_client

    return SnapshotFetchResult(
        snapshots=all_snapshots,
        snapshot_id_to_client=snapshot_id_to_client,
        failed_downloaders=failed_downloaders,
    )
