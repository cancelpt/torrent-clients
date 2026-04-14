"""Public package exports for torrent_clients."""

from torrent_clients.client_helper import fetch_torrents, get_downloader_client

__all__ = ["get_downloader_client", "fetch_torrents"]
