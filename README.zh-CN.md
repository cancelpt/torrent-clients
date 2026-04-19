# torrent-clients（简体中文）

[![CI](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml/badge.svg)](https://github.com/cancelpt/torrent-clients/actions/workflows/ci.yml)

`torrent_clients` 提供了对 qBittorrent 和 Transmission 的统一 Python 封装。

项目/发行名为 `torrent-clients`；Python 包与导入名为 `torrent_clients`。

## 安装

```bash
pip install -e .
```

## 支持的下载器版本

- qBittorrent：仓库自有 transport 当前文档化支持 qBittorrent `4.x` 到 `5.0.1`，并兼容 qB 5 的 `start/stop` 与旧版 `resume/pause` 控制命名差异。
- Transmission：仓库自有 transport 当前文档化支持 Transmission `2.40` 到 `4.0.6`。

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
    username="<your-transmission-username>",
    password="<your-transmission-password>",
    dl_type=ClientType.TRANSMISSION,
    name="tr",
)
tr_client.login()

qb_client.reannounce_torrent("torrent-hash")
tr_client.move_queue([1, 2], QueueDirection.TOP)

snapshots = qb_client.get_torrents_snapshot()
summary = tr_client.get_torrents(query=TorrentQuery(torrent_ids=[1, 2]))
detail = tr_client.get_torrent_info(1)
hydrated = tr_client.hydrate_files([1, 2])
```

## 通用接口

qB 和 TR 都支持以下接口：

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

其中 `torrent_id_or_ids` 支持单个 ID（`int` 或 `str`）或 ID 列表。

`QueueDirection` 可选值为：`TOP`、`BOTTOM`、`UP`、`DOWN`。
调用 `move_queue(...)` 时，`direction` 为必填参数。

## 统一查询对象 TorrentQuery

```python
from torrent_clients.client.base_client import TorrentQuery

query = TorrentQuery(
    torrent_ids=[1, 2, 3],  # 共享契约
)

torrents = qb_client.get_torrents(query=query)
```

下载器特有筛选项仍只保留为过渡期兼容行为，不属于文档化的通用契约。

`TorrentQuery.fields` 只保留一个过渡版本的兼容用途。新代码应改用：

- `get_torrents()` 获取 summary 列表
- `get_torrent_info()` 获取单个种子的 eager detail
- `hydrate_files()` / `hydrate_trackers()` 显式拉取重字段

## 说明

- `rename_torrent(..., new_name=...)` 仅 qB 支持。
- Transmission 的重命名能力使用 `old_path + new_path`（底层 `rename_torrent_path`）。
- `get_torrents()` 和 `get_torrents_snapshot()` 不会再进行隐藏的远程 lazy 加载。`files`、`trackers`、`comment` 等重字段只会由 `get_torrent_info()` 或 `hydrate_*()` 填充。
- `get_torrents_original()`、`get_torrents_lazy()`、`SupportsLazyTorrentFetch`、`TorrentQuery.fields` 都属于过渡兼容层，调用时会立即发出 `DeprecationWarning`。

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
from torrent_clients.client.base_client import SupportsCategoryManagement, SupportsIpBan

if qb_client.supports_capability(SupportsIpBan):
    qb_client.require_capability(SupportsIpBan).ban_ips(["1.2.3.4"])

if qb_client.supports_capability(SupportsCategoryManagement):
    qb_client.require_capability(SupportsCategoryManagement).set_category(
        "torrent-hash",
        "movies",
    )
```

如果客户端不支持该能力，`require_capability(...)` 会抛出 `UnsupportedClientCapabilityError`。

## 已弃用的兼容 API

以下 API 只在过渡版本中保留，并且一旦调用就会发出 `DeprecationWarning`：

- `TransmissionClient.get_torrents_lazy(...)`
- `SupportsLazyTorrentFetch`
- `TorrentQuery.fields`
- `QbittorrentClient.get_torrents_original(...)`

## 本地联调地址

- qBittorrent：`http://localhost:8080/`
- Transmission：`http://localhost:9091/`（请使用你自己的账号密码）

## 测试配置

`tests/test_client_interfaces.py` 通过环境变量读取测试地址和凭据：

- `TEST_QB_URL`（默认：`http://localhost:8080/`）
- `TEST_QB_USERNAME`（默认：空字符串）
- `TEST_QB_PASSWORD`（默认：空字符串）
- `TEST_TR_URL`（默认：`http://localhost:9091/`）
- `TEST_TR_USERNAME`（默认：空字符串）
- `TEST_TR_PASSWORD`（默认：空字符串）

示例：

```bash
export TEST_QB_URL="http://localhost:8080/"
export TEST_QB_USERNAME=""
export TEST_QB_PASSWORD=""
export TEST_TR_URL="http://localhost:9091/"
export TEST_TR_USERNAME="test_user"
export TEST_TR_PASSWORD="test_password"
python -m pytest tests/test_client_interfaces.py
```

## 开发

```bash
pre-commit install
pytest
```
