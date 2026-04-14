from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from os.path import splitext
from typing import Any, Generator, List, Optional, Union

from pydantic import BaseModel


class TorrentFile(BaseModel):
    name: str
    size: Optional[Union[int, float]]
    priority: Optional[int] = 0
    wanted: Optional[bool] = True
    completed_size: Optional[Union[int, float]] = 0

    @property
    def path(self) -> str:
        return self.name.replace("\\", "/")

    @property
    def extension(self) -> str:
        return splitext(self.path.lower())[1]


class TorrentFileList(Sequence, ABC):
    def __init__(self, torrent_id: int | str, raw: List[Any]):
        self.torrent_id = torrent_id
        self.raw = raw or []
        self._details_cache: Optional[List[TorrentFile]] = None

    @abstractmethod
    def transform(self, file_data: List[Any]) -> TorrentFile:
        pass

    def _build_details(self) -> List[TorrentFile]:
        return [self.transform(list(file_data)) for file_data in zip(*self.raw)]

    def _ensure_details(self) -> List[TorrentFile]:
        if self._details_cache is None:
            self._details_cache = self._build_details()
        return self._details_cache

    def __iter__(self) -> Generator[TorrentFile, None, None]:
        yield from self._ensure_details()

    def __len__(self) -> int:
        if self._details_cache is not None:
            return len(self._details_cache)
        return len(self.raw[0]) if self.raw else 0

    def __getitem__(self, index: Union[int, slice]) -> Union[TorrentFile, List[TorrentFile]]:
        details = self._ensure_details()
        if not details:
            raise IndexError("TorrentFileList is empty")
        return details[index]

    @property
    def details(self) -> List[TorrentFile]:
        return self._ensure_details()

    def iter_file_entries(self):  # type: ignore[no-untyped-def]
        for file_detail in self._ensure_details():
            normalized_name = str(
                getattr(file_detail, "path", "") or getattr(file_detail, "name", "") or ""
            ).replace("\\", "/")
            origin_name = str(
                getattr(file_detail, "name", "") or getattr(file_detail, "path", "") or ""
            )
            if not normalized_name or not origin_name:
                continue
            yield {
                "path": normalized_name,
                "origin": origin_name,
                "size": int(getattr(file_detail, "size", 0) or 0),
            }

    def iter_path_names(self):  # type: ignore[no-untyped-def]
        for file_entry in self.iter_file_entries():
            normalized_name = str(file_entry.get("path", "") or "")
            origin_name = str(file_entry.get("origin", "") or "")
            if not normalized_name or not origin_name:
                continue
            yield normalized_name, origin_name
