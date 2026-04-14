from __future__ import annotations

import pytest

from torrent_clients.torrent.torrent_status import (
    DownloaderKind,
    TorrentStatus,
    convert_status,
    is_completed,
    is_downloading,
    is_seeding,
    is_stopped,
)


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        (0, TorrentStatus.STOPPED),
        (1, TorrentStatus.CHECKING),
        (2, TorrentStatus.CHECKING),
        (3, TorrentStatus.QUEUED),
        (4, TorrentStatus.DOWNLOADING),
        (5, TorrentStatus.QUEUED),
        (6, TorrentStatus.SEEDING),
    ],
)
def test_transmission_status_mapping(raw_status: int, expected: TorrentStatus) -> None:
    assert convert_status(raw_status, DownloaderKind.TRANSMISSION) is expected


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("pausedUP", TorrentStatus.STOPPED),
        ("pausedDL", TorrentStatus.STOPPED),
        ("uploading", TorrentStatus.SEEDING),
        ("queuedDL", TorrentStatus.QUEUED),
        ("checkingResumeData", TorrentStatus.CHECKING),
        ("metaDL", TorrentStatus.METADATA),
    ],
)
def test_qb_status_mapping(raw_status: str, expected: TorrentStatus) -> None:
    assert convert_status(raw_status, DownloaderKind.QBITTORRENT) is expected


def test_unknown_raw_status_returns_unknown() -> None:
    assert convert_status("totally-unknown", DownloaderKind.QBITTORRENT) is TorrentStatus.UNKNOWN


def test_invalid_downloader_kind_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported downloader kind"):
        convert_status("downloading", "bad-source")


def test_status_predicates_are_domain_semantic() -> None:
    assert is_downloading(TorrentStatus.METADATA)
    assert is_downloading(TorrentStatus.DOWNLOADING)
    assert not is_downloading(TorrentStatus.QUEUED)

    assert is_seeding(TorrentStatus.SEEDING)
    assert not is_seeding(TorrentStatus.STOPPED)

    assert is_stopped(TorrentStatus.STOPPED)
    assert not is_stopped(TorrentStatus.CHECKING)


def test_completion_is_progress_based() -> None:
    assert is_completed(1.0)
    assert is_completed(1.2)
    assert not is_completed(0.999)
    assert not is_completed(None)
