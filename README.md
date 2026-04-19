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

- qBittorrent: the repository-owned transport currently targets qBittorrent `4.x` through `5.1.x`, including qB 5 `start/stop` vs legacy `resume/pause` control compatibility, official `/torrents/info` filter/hash translation, and qB `5.1` add/cookie/tag/WebSeed compatibility.
- Transmission: the repository-owned transport currently targets Transmission `2.40` through `4.0.6`.

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

snapshots = qb_client.get_torrents_snapshot()
summary = tr_client.get_torrents(query=TorrentQuery(torrent_ids=[1, 2]))
detail = tr_client.get_torrent_info(1)
hydrated = tr_client.hydrate_files([1, 2])
```

## Common API

Both clients implement these core methods:

- `add_torrent(...)`
- `remove_torrent(torrent_id_or_ids, delete_data=False)`
- `get_torrents_snapshot(status=None, query=None)`
- `get_torrents(status=None, query=None)`
- `get_torrent_info(torrent_id)`
- `hydrate_files(torrent_id_or_ids)`
- `hydrate_trackers(torrent_id_or_ids)`
- `move_torrent(torrent, download_dir, move_files=True) -> bool`
- `set_labels(...)`
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
    torrent_ids=[1, 2, 3],  # shared contract
)

torrents = qb_client.get_torrents(status=None, query=query)
```

Downloader-specific selectors remain transition-only compatibility behavior and are intentionally not part of the documented common contract.

`TorrentQuery.fields` remains a deprecated compatibility shim for one transition release. New code should use:

- `get_torrents()` for summary lists
- `get_torrent_info()` for eager single-torrent detail
- `hydrate_files()` / `hydrate_trackers()` for explicit heavy-field retrieval

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
- `get_torrents()` and `get_torrents_snapshot()` do not perform hidden remote lazy-loading. Heavy fields such as `files`, `trackers`, and `comment` are populated only by `get_torrent_info()` or `hydrate_*()`.
- `get_torrents_original()`, `get_torrents_lazy()`, `SupportsLazyTorrentFetch`, and `TorrentQuery.fields` are deprecated compatibility surface and emit `DeprecationWarning` on use.

## Downloader-specific Capabilities

Use capability checks for downloader-specific features:

```python
from torrent_clients.client.base_client import SupportsCategoryManagement, SupportsIpBan

if qb_client.supports_capability(SupportsIpBan):
    qb_client.require_capability(SupportsIpBan).ban_ips(["1.2.3.4"])

if qb_client.supports_capability(SupportsCategoryManagement):
    qb_client.require_capability(SupportsCategoryManagement).set_category(
        "torrent-hash",
        "movies",
    )
```

`require_capability(...)` raises `UnsupportedClientCapabilityError` when unsupported.

## Deprecated Compatibility APIs

The following APIs remain available only during the transition release and emit `DeprecationWarning` immediately on use:

- `TransmissionClient.get_torrents_lazy(...)`
- `SupportsLazyTorrentFetch`
- `TorrentQuery.fields`
- `QbittorrentClient.get_torrents_original(...)`

## Local Integration Endpoints

- qBittorrent: `http://localhost:8080/`
- Transmission: `http://localhost:9091/` (use your own credentials)

## Test Configuration

`tests/test_client_interfaces.py` loads endpoints and credentials from environment variables:

- `TEST_QB_URL` (default: `http://localhost:8080/`)
- `TEST_QB_USERNAME` (default: empty)
- `TEST_QB_PASSWORD` (default: empty)
- `TEST_TR_URL` (default: `http://localhost:9091/`)
- `TEST_TR_USERNAME` (default: empty)
- `TEST_TR_PASSWORD` (default: empty)

Example:

```bash
export TEST_QB_URL="http://localhost:8080/"
export TEST_QB_USERNAME=""
export TEST_QB_PASSWORD=""
export TEST_TR_URL="http://localhost:9091/"
export TEST_TR_USERNAME="test_user"
export TEST_TR_PASSWORD="test_password"
python -m pytest tests/test_client_interfaces.py
```

## Development

```bash
pre-commit install
pytest
```
