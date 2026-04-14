from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Dict, Generator, List, Optional, Union

from pydantic import BaseModel


class TorrentPeer(BaseModel):
    client: str
    dl_speed: int
    downloaded: int
    up_speed: int
    uploaded: int
    ip: str
    port: int
    progress: float
    flags: str


class TorrentPeerList(Sequence, ABC):
    def __init__(self, raw: Dict[str, Any] | List[Dict[str, Any]] | None):
        self.raw = raw or {}

    @abstractmethod
    def transform(self, peer_data: Dict[str, Any]) -> Optional[TorrentPeer]:
        pass

    def __iter__(self) -> Generator[TorrentPeer, None, None]:
        if isinstance(self.raw, dict):
            iterator = self.raw.values()
        else:
            iterator = self.raw

        for peer_data in iterator:
            yield self.transform(peer_data)

    def __len__(self) -> int:
        return len(self.raw)

    def __getitem__(self, index: Union[int, slice]) -> Union[TorrentPeer, List[TorrentPeer]]:
        # Handle dict raw data by converting values to list for indexing
        # This is a bit expensive but necessary for random access on dict wrapper
        # We only convert the values to a list of references
        if isinstance(self.raw, dict):
            raw_list = list(self.raw.values())
        else:
            raw_list = self.raw

        if isinstance(index, slice):
            return [self.transform(data) for data in raw_list[index]]

        return self.transform(raw_list[index])

    @property
    def details(self) -> List[TorrentPeer]:
        if not self.raw:
            return []

        if isinstance(self.raw, dict):
            iterator = self.raw.values()
        else:
            iterator = self.raw

        return [self.transform(peer_data) for peer_data in iterator]
