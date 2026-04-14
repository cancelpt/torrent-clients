from __future__ import annotations

import os

import pytest

from torrent_clients.client.client_type import ClientType
from torrent_clients.client.qbittorrent_client import QbittorrentClient
from torrent_clients.client.transmission_client import TransmissionClient
from torrent_clients.client_helper import fetch_torrents, get_downloader_client


def _test_qb_config() -> tuple[str, str, str]:
    return (
        os.getenv("TEST_QB_URL", "http://localhost:18080/"),
        os.getenv("TEST_QB_USERNAME", ""),
        os.getenv("TEST_QB_PASSWORD", ""),
    )


def _test_tr_config() -> tuple[str, str, str]:
    return (
        os.getenv("TEST_TR_URL", "http://localhost:9091/"),
        os.getenv("TEST_TR_USERNAME", ""),
        os.getenv("TEST_TR_PASSWORD", ""),
    )


def test_get_downloader_client_accepts_enum_for_qb() -> None:
    url, username, password = _test_qb_config()
    client = get_downloader_client(
        url=url,
        username=username,
        password=password,
        dl_type=ClientType.QBITTORRENT,
        name="qb",
    )

    assert isinstance(client, QbittorrentClient)


def test_get_downloader_client_accepts_string_for_transmission() -> None:
    url, username, password = _test_tr_config()
    client = get_downloader_client(
        url=url,
        username=username,
        password=password,
        dl_type=ClientType.TRANSMISSION.value,
        name="tr",
    )

    assert isinstance(client, TransmissionClient)


def test_get_downloader_client_rejects_invalid_type() -> None:
    with pytest.raises(ValueError, match="Invalid client type"):
        url, username, password = _test_qb_config()
        get_downloader_client(
            url=url,
            username=username,
            password=password,
            dl_type="invalid",
            name="bad",
        )


def test_fetch_torrents_collects_mapping_and_failed_downloaders() -> None:
    class _Downloader:
        def __init__(self, name: str, enabled: bool = True):
            tr_url, tr_username, tr_password = _test_tr_config()
            self.name = name
            self.enabled = enabled
            self.url = tr_url
            self.username = tr_username
            self.password = tr_password
            self.dl_type = "qb"

    class _Client:
        def __init__(self, torrents):
            self._torrents = torrents

        def get_torrents(self):
            return self._torrents

    downloaders = [_Downloader("ok"), _Downloader("bad")]

    def _factory(url, username, password, dl_type, name):
        if name == "bad":
            raise RuntimeError("connect failed")
        return _Client([type("Torrent", (), {"hash_string": "h1"})()])

    result = fetch_torrents(downloaders, client_factory=_factory)

    assert len(result.torrents) == 1
    assert "h1" in result.torrent_id_to_client
    assert result.failed_downloaders == ["bad"]


def test_fetch_torrents_skips_disabled_downloader_by_default() -> None:
    class _Downloader:
        def __init__(self, name: str, enabled: bool):
            tr_url, tr_username, tr_password = _test_tr_config()
            self.name = name
            self.enabled = enabled
            self.url = tr_url
            self.username = tr_username
            self.password = tr_password
            self.dl_type = "qb"

    calls = []

    def _factory(url, username, password, dl_type, name):
        calls.append(name)
        return type("Client", (), {"get_torrents": lambda self: []})()

    downloaders = [_Downloader("enabled", True), _Downloader("disabled", False)]

    fetch_torrents(downloaders, client_factory=_factory)
    assert calls == ["enabled"]

    calls.clear()
    fetch_torrents(downloaders, include_disabled=True, client_factory=_factory)
    assert calls == ["enabled", "disabled"]
