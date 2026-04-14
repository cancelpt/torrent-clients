# torrent-clients（简体中文）

[![CI](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml/badge.svg)](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml)

`torrent_clients` 提供了对 qBittorrent 和 Transmission 的统一 Python 封装。

项目/发行名为 `torrent-clients`；Python 包与导入名为 `torrent_clients`。

## 安装

```bash
pip install -e .
```

## 快速开始

```python
from torrent_clients.client.base_client import QueueDirection, TorrentQuery
from torrent_clients.client.client_type import ClientType
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
    username="<你的 Transmission 用户名>",
    password="<你的 Transmission 密码>",
    dl_type=ClientType.TRANSMISSION,
    name="tr",
)
tr_client.login()

qb_client.reannounce_torrent("torrent-hash")
tr_client.move_queue([1, 2], QueueDirection.TOP)

query = TorrentQuery(fields=["id", "name", "status"])
tr_torrents = tr_client.get_torrents(query=query)
```

## 通用接口

qB 和 TR 都支持以下接口：

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

其中 `torrent_id_or_ids` 支持单个 ID（`int` 或 `str`）或 ID 列表。

`QueueDirection` 可选值为：`TOP`、`BOTTOM`、`UP`、`DOWN`。
调用 `move_queue(...)` 时，`direction` 为必填参数。

## 统一查询对象 TorrentQuery

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

torrents = tr_client.get_torrents(query=query)
```

## 说明

- `rename_torrent(..., new_name=...)` 仅 qB 支持。
- Transmission 的重命名能力使用 `old_path + new_path`（底层 `rename_torrent_path`）。

## 推荐集成模式（适用于 `torrent-transfer`）

使用 helper 一次返回种子列表与 `hash -> client` 映射：

```python
from torrent_clients.client_helper import fetch_torrents

result = fetch_torrents(downloaders)
if result.failed_downloaders:
    raise RuntimeError(f"failed downloaders: {result.failed_downloaders}")

all_torrents = result.torrents
torrent_id_to_client = result.torrent_id_to_client
```

对缓存/快照驱动场景，使用 snapshot helper：

```python
from torrent_clients.client_helper import fetch_torrent_snapshots

result = fetch_torrent_snapshots(downloaders)
if result.failed_downloaders:
    raise RuntimeError(f"failed downloaders: {result.failed_downloaders}")

all_snapshots = result.snapshots
snapshot_id_to_client = result.snapshot_id_to_client
```

## 下载器特有能力（Capability）

```python
from torrent_clients.client.base_client import SupportsIpBan, SupportsLazyTorrentFetch

if qb_client.supports_capability(SupportsIpBan):
    qb_client.require_capability(SupportsIpBan).ban_ips(["1.2.3.4"])

if tr_client.supports_capability(SupportsLazyTorrentFetch):
    lazy_torrents = tr_client.require_capability(
        SupportsLazyTorrentFetch
    ).get_torrents_lazy()
```

如果客户端不支持该能力，`require_capability(...)` 会抛出 `UnsupportedClientCapabilityError`。

## Transmission 的 Lazy / Strict 行为

`TransmissionClient.get_torrents_lazy(...)` 有两种模式：

- `arguments=None`：默认 hybrid 模式。标量字段会预取，重字段走 lazy/hybrid。
- `arguments=[...]`：严格模式。只请求你指定的字段，禁用自动 lazy/hybrid 补拉。

严格模式下，访问未请求字段会抛出 `MissingTorrentFieldError`。

## 本地联调地址

- qBittorrent：`http://localhost:8080/`
- Transmission：`http://localhost:9091/`（请使用你自己的账号密码）

## 测试配置

`tests/test_client_interfaces.py` 通过环境变量读取测试地址和凭据：

- `TEST_QB_URL`（默认：`http://127.0.0.1:8080/`）
- `TEST_QB_USERNAME`（默认：空字符串）
- `TEST_QB_PASSWORD`（默认：空字符串）
- `TEST_TR_URL`（默认：`http://127.0.0.1:9091/`）
- `TEST_TR_USERNAME`（默认：空字符串）
- `TEST_TR_PASSWORD`（默认：空字符串）

示例：

```bash
export TEST_QB_URL="http://127.0.0.1:8080/"
export TEST_QB_USERNAME=""
export TEST_QB_PASSWORD=""
export TEST_TR_URL="http://127.0.0.1:9091/"
export TEST_TR_USERNAME="your_username"
export TEST_TR_PASSWORD="your_password"
python -m pytest tests/test_client_interfaces.py
```

## 开发

```bash
pre-commit install
pytest
```
