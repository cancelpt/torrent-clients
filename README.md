# torrent-clients

[![CI](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml/badge.svg)](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml)

Unified Python client wrappers for qBittorrent and Transmission.

Project/distribution name: `torrent-clients`; Python package/import name: `torrent_clients`.

For Chinese documentation, see [README.zh-CN.md](README.zh-CN.md).

## Install

```bash
pip install -e .
```

## Supported Downloader Versions

- qBittorrent: this project currently pins `qbittorrent-api>=2024.10.68,<2024.11`. Upstream `qbittorrent-api` v2024.10.68 is a qBittorrent Web API client for qBittorrent `v4.1+` and explicitly advertises support for qBittorrent `v5.0.1` (Web API `v2.11.2`). In practice, this repository currently targets qBittorrent `4.x` through `5.0.1`, and includes compatibility for the qB 5 `start/stop` vs legacy `resume/pause` naming change. qBittorrent `5.1+` may work, but is not currently documented as supported by this pinned dependency line.
- Transmission: this project currently pins `transmission-rpc>=7.0.11,<8`. Upstream `transmission-rpc` v7.0.11 documents support for Transmission `2.40` through `4.0.6`. Newer Transmission releases may still work, but newer RPC fields or features may be missing until the dependency is updated.

## Quick Start

```python
from torrent_clients.client.client_type import ClientType
from torrent_clients.client.base_client import QueueDirection, TorrentQuery
from torrent_clients.client_helper import get_downloader_client

qb_client = get_downloader_client(
    url="http://localhost:8080/",
    username="",
    password="",
    dl_type=ClientType.QBITTORRENT,
    name="qb",
)
qb_client.login()

tr_client = get_downloader_client(
    url="http://localhost:9091/",
    username="<your-transmission-username>",
    password="<your-transmission-password>",
    dl_type=ClientType.TRANSMISSION,
    name="tr",
)
tr_client.login()

# unified optional operations
qb_client.reannounce_torrent("torrent-hash")
tr_client.move_queue([1, 2], QueueDirection.TOP)

query = TorrentQuery(fields=["id", "name", "status"])
tr_torrents = tr_client.get_torrents(query=query)
```

## Common API

Both clients implement these core methods:

- `add_torrent(...)`
- `remove_torrent(torrent_id_or_ids, delete_data=False)`
- `get_torrents(status=None, query=None)`
- `get_torrent_info(torrent_id)`
- `move_torrent(torrent, download_dir, move_files=True) -> bool`
- `set_labels(...)`
- `set_category(...)`
- `resume_torrent(torrent_id_or_ids)`
- `pause_torrent(torrent_id_or_ids)`
- `recheck_torrent(torrent_id_or_ids)`
- `reannounce_torrent(torrent_id_or_ids)`
- `set_torrent_limits(torrent_id_or_ids, download_limit=None, upload_limit=None)`
- `move_queue(torrent_id_or_ids, direction)`
- `set_files(torrent_id, file_ids, wanted=None, priority=None)`
- `list_trackers(torrent_id)`
- `add_trackers(torrent_id, tracker_urls)`
- `remove_trackers(torrent_id, tracker_urls)`
- `replace_tracker(torrent_id, old_url, new_url)`
- `rename_torrent(torrent_id, new_name=None, old_path=None, new_path=None)`
- `set_global_limits(download_limit=None, upload_limit=None)`
- `get_client_stats()`
- `get_peer_info(torrent_id)`

`torrent_id_or_ids` accepts a single id (`int` or `str`) or a sequence of ids.

`QueueDirection` values: `TOP`, `BOTTOM`, `UP`, `DOWN`.
`direction` is required when calling `move_queue(...)`.

### Query Object

Use `TorrentQuery` for unified list retrieval options:

```python
from torrent_clients.client.base_client import TorrentQuery

query = TorrentQuery(
    category="movies",      # qB
    tag="private",          # qB
    sort="name",            # qB
    reverse=True,           # qB
    limit=50,               # qB
    offset=0,               # qB
    torrent_ids=[1, 2, 3],  # qB + TR
    fields=["id", "name"],  # TR
)

torrents = tr_client.get_torrents(status=None, query=query)
```

### Recommended Integration Pattern (for `torrent-transfer`)

Use the helper that returns both torrent list and `hash -> client` mapping:

```python
from torrent_clients.client_helper import fetch_torrents

result = fetch_torrents(downloaders)
if result.failed_downloaders:
    raise RuntimeError(f"failed downloaders: {result.failed_downloaders}")

all_torrents = result.torrents
torrent_id_to_client = result.torrent_id_to_client
```

For cache-driven workflows, use snapshot fetch:

```python
from torrent_clients.client_helper import fetch_torrent_snapshots

result = fetch_torrent_snapshots(downloaders)
if result.failed_downloaders:
    raise RuntimeError(f"failed downloaders: {result.failed_downloaders}")

all_snapshots = result.snapshots
snapshot_id_to_client = result.snapshot_id_to_client
```

## Notes

- `rename_torrent(..., new_name=...)` is qBittorrent-only.
- Transmission supports rename via `old_path + new_path` (RPC `rename_torrent_path`).

## Downloader-specific Capabilities

Use capability checks for downloader-specific features:

```python
from torrent_clients.client.base_client import SupportsIpBan, SupportsLazyTorrentFetch

if qb_client.supports_capability(SupportsIpBan):
    qb_client.require_capability(SupportsIpBan).ban_ips(["1.2.3.4"])

if tr_client.supports_capability(SupportsLazyTorrentFetch):
    lazy_torrents = tr_client.require_capability(
        SupportsLazyTorrentFetch
    ).get_torrents_lazy()
```

`require_capability(...)` raises `UnsupportedClientCapabilityError` when unsupported.

## Transmission Lazy/Strict Behavior

`TransmissionClient.get_torrents_lazy(...)` has two modes:

- `arguments=None`: default hybrid mode. Scalar fields are prefetched, heavy fields are lazy/hybrid fetched.
- `arguments=[...]`: strict mode. Only user-defined fields are fetched, and auto lazy/hybrid fetch is disabled.

In strict mode, accessing a non-requested field raises `MissingTorrentFieldError`.

## Local Integration Endpoints

- qBittorrent: `http://localhost:8080/`
- Transmission: `http://localhost:9091/` (use your own credentials)

## Test Configuration

`tests/test_client_interfaces.py` loads endpoints and credentials from environment variables:

- `TEST_QB_URL` (default: `http://127.0.0.1:8080/`)
- `TEST_QB_USERNAME` (default: empty)
- `TEST_QB_PASSWORD` (default: empty)
- `TEST_TR_URL` (default: `http://127.0.0.1:9091/`)
- `TEST_TR_USERNAME` (default: empty)
- `TEST_TR_PASSWORD` (default: empty)

Example:

```bash
export TEST_QB_URL="http://127.0.0.1:8080/"
export TEST_QB_USERNAME=""
export TEST_QB_PASSWORD=""
export TEST_TR_URL="http://127.0.0.1:9091/"
export TEST_TR_USERNAME="your_username"
export TEST_TR_PASSWORD="your_password"
python -m pytest tests/test_client_interfaces.py
```

## Development

```bash
pre-commit install
pytest
```
