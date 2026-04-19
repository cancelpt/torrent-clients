from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Callable, Dict, Generator, List, Optional, Union

from pydantic import BaseModel

from torrent_clients.torrent.torrent_status import TorrentStatus


class TorrentInfo(BaseModel):
    id: Optional[Union[int, str]] = None
    name: str
    hash_string: str
    download_dir: Optional[str] = None
    size: Optional[int] = 0
    progress: Optional[float] = 0
    status: Optional[TorrentStatus] = TorrentStatus.UNKNOWN
    download_speed: Optional[int] = 0
    upload_speed: Optional[int] = 0
    files: Optional[Any] = None
    trackers: Optional[Any] = None
    completed_size: Optional[int] = 0
    uploaded_size: Optional[int] = 0
    selected_size: Optional[int] = 0
    labels: Optional[list] = None
    category: Optional[str] = None
    num_leechs: Optional[int] = -1
    num_seeds: Optional[int] = -1
    added_on: Optional[int] = 0
    peers: Optional[Any] = None
    comment: Optional[Any] = None


class TorrentList(Sequence, ABC):
    def __init__(self, raw: List[Dict[str, Any]] | None = None):
        self.raw = raw or []

    @abstractmethod
    def transform(self, torrent_data: Dict[str, Any]) -> TorrentInfo:
        pass

    def __iter__(self) -> Generator[TorrentInfo, None, None]:
        for torrent_data in self.raw:
            yield self.transform(torrent_data)

    def __len__(self) -> int:
        return len(self.raw)

    def __getitem__(self, index: Union[int, slice]) -> Union[TorrentInfo, List[TorrentInfo]]:
        if isinstance(index, slice):
            return [self.transform(data) for data in self.raw[index]]

        return self.transform(self.raw[index])

    @property
    def details(self) -> List[TorrentInfo]:
        return [self.transform(torrent_data) for torrent_data in self.raw]


class LazyProxy:
    def __init__(self, loader: Callable[[], Any]):
        object.__setattr__(self, "_loader", loader)
        object.__setattr__(self, "_target", None)

    def _ensure_target(self) -> Any:
        if self._target is None:
            object.__setattr__(self, "_target", self._loader())
        return self._target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ensure_target(), name)

    def __iter__(self):
        return iter(self._ensure_target())

    def __len__(self):
        return len(self._ensure_target())

    def __getitem__(self, key: Any) -> Any:
        return self._ensure_target()[key]

    def __repr__(self):
        if self._target is None:
            return "<LazyProxy (unloaded)>"
        return repr(self._target)

    def __bool__(self):
        return bool(self._ensure_target())

    def __str__(self):
        return str(self._ensure_target())

    def __eq__(self, other: Any) -> bool:
        return self._ensure_target() == other
