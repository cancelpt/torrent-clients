from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Dict, Generator, List, Optional, Union

from pydantic import BaseModel


class TorrentTracker(BaseModel):
    url: str
    seeder: int
    leecher: int
    downloaded: int
    peers: int
    info: Optional[str] = None


class TorrentTrackerList(Sequence, ABC):
    valid_url_pattern = re.compile(r"^(udp|http|https)://")

    def __init__(self, raw: List[Dict[str, Any]] | None):
        self.raw = raw or []

    @abstractmethod
    def transform(self, tracker_data: Dict[str, Any]) -> Optional[TorrentTracker]:
        pass

    def __iter__(self) -> Generator[TorrentTracker, None, None]:
        for tracker_data in self.raw:
            parsed_tracker = self.transform(tracker_data)
            if parsed_tracker is not None:
                yield parsed_tracker

    def __len__(self) -> int:
        return len(self.raw)

    def __getitem__(
        self, index: Union[int, slice]
    ) -> Union[Optional[TorrentTracker], List[Optional[TorrentTracker]]]:
        if isinstance(index, slice):
            return [self.transform(data) for data in self.raw[index]]
        return self.transform(self.raw[index])

    @property
    def details(self) -> List[TorrentTracker]:
        return [
            tracker
            for tracker in (self.transform(tracker_data) for tracker_data in self.raw)
            if tracker is not None
        ]
