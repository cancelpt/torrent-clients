"""Repository-owned qBittorrent Web API transport."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from torrent_clients.transport.errors import (
    TransportAuthenticationError,
    TransportProtocolError,
)
from torrent_clients.transport.http import HttpSession

_API_PREFIX = "/api/v2"
_START_STOP_WEB_API_THRESHOLD = (2, 11, 0)
_MODERN_ADD_WEB_API_THRESHOLD = (2, 11, 3)
_SET_TAGS_WEB_API_THRESHOLD = (2, 11, 4)


def _normalize_bool(value: bool) -> str:
    return "true" if value else "false"


def _coerce_hashes(torrent_hashes: str | Sequence[str] | None) -> str:
    if torrent_hashes is None:
        return ""
    if isinstance(torrent_hashes, str):
        return torrent_hashes
    return "|".join(str(item) for item in torrent_hashes)


def _coerce_urls(urls: str | Sequence[str] | None, *, separator: str) -> str:
    if urls is None:
        return ""
    if isinstance(urls, str):
        return urls
    return separator.join(str(url) for url in urls if str(url))


def _coerce_tags(tags: str | Sequence[str] | None) -> str:
    if tags is None:
        return ""
    if isinstance(tags, str):
        return tags
    return ",".join(str(tag) for tag in tags if str(tag))


def _build_payload(
    values: Mapping[str, Any],
    *,
    bool_keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    bool_key_set = set(bool_keys or [])
    payload: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if key in bool_key_set and isinstance(value, bool):
            payload[key] = _normalize_bool(value)
            continue
        payload[key] = value
    return payload


def _normalize_info_params(values: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if key == "status_filter":
            params["filter"] = value
            continue
        if key == "torrent_hashes":
            hashes = _coerce_hashes(value)
            if hashes:
                params["hashes"] = hashes
            continue
        if key == "fields":
            continue
        params[key] = value
    return params


def _coerce_content_layout(root_folder: bool | None) -> str | None:
    if root_folder is None:
        return None
    return "Original" if root_folder else "NoSubfolder"


def _parse_version_components(raw_version: str) -> tuple[int, int, int] | None:
    version_parts = [int(part) for part in re.findall(r"\d+", raw_version)]
    if not version_parts:
        return None
    padded = (version_parts + [0, 0, 0])[:3]
    return tuple(padded)  # type: ignore[return-value]


class QbittorrentTransport:
    """Transport implementation for qBittorrent Web API."""

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 15.0,
        session: HttpSession | None = None,
    ) -> None:
        self.base_url = base_url
        self.username = username or ""
        self.password = password or ""
        self.session = session or HttpSession(base_url, timeout=timeout)
        self._logged_in = False
        self._web_api_version_components: tuple[int, int, int] | None = None
        self._web_api_version_loaded = False

    def _parsed_web_api_version(self) -> tuple[int, int, int] | None:
        if not self._web_api_version_loaded:
            self._web_api_version_components = _parse_version_components(self.app_web_api_version())
            self._web_api_version_loaded = True
        return self._web_api_version_components

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int | Iterable[int] = 200,
        retry_auth: bool = True,
        auth_required: bool = True,
        **kwargs,
    ):
        if auth_required:
            self.auth_log_in()

        response = self.session.request(method, path, **kwargs)
        if auth_required and retry_auth and response.status_code == 403:
            self._logged_in = False
            self.auth_log_in()
            response = self.session.request(method, path, **kwargs)

        if isinstance(expected_status, int):
            allowed_status = {expected_status}
        else:
            allowed_status = set(expected_status)

        if response.status_code not in allowed_status:
            raise TransportProtocolError(
                f"unexpected HTTP status {response.status_code} for {method.upper()} {path}"
            )
        return response

    def _request_json(self, method: str, path: str, **kwargs):
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise TransportProtocolError(
                f"invalid JSON response for {method.upper()} {path}"
            ) from exc

    def _request_action(self, path: str, *, data: Mapping[str, Any] | None = None, **kwargs):
        response = self._request("POST", path, data=data, **kwargs)
        result = (response.text or "").strip()
        return {"ok": result in {"", "Ok.", "ok", "OK"}, "result": result}

    def _resolve_lifecycle_endpoint(self, modern_path: str, legacy_path: str) -> str:
        parsed = self._parsed_web_api_version()
        if parsed is not None and parsed >= _START_STOP_WEB_API_THRESHOLD:
            return modern_path
        return legacy_path

    def auth_log_in(self) -> bool:
        if self._logged_in:
            return True

        response = self._request(
            "POST",
            f"{_API_PREFIX}/auth/login",
            expected_status=(200, 401, 403),
            data={
                "username": self.username,
                "password": self.password,
            },
            auth_required=False,
            retry_auth=False,
        )
        result = (response.text or "").strip()
        if response.status_code != 200 or result != "Ok.":
            raise TransportAuthenticationError("qBittorrent authentication failed")
        self._logged_in = True
        return True

    def app_web_api_version(self) -> str:
        response = self._request("GET", f"{_API_PREFIX}/app/webapiVersion")
        return (response.text or "").strip()

    def app_preferences(self):
        payload = self._request_json("GET", f"{_API_PREFIX}/app/preferences")
        if not isinstance(payload, dict):
            raise TransportProtocolError("invalid qBittorrent app preferences payload")
        return payload

    def app_cookies(self):
        payload = self._request_json("GET", f"{_API_PREFIX}/app/cookies")
        if not isinstance(payload, list):
            raise TransportProtocolError("invalid qBittorrent cookies payload")
        return payload

    def app_set_preferences(self, *, prefs: Mapping[str, Any]):
        return self._request_action(
            f"{_API_PREFIX}/app/setPreferences",
            data={"json": json.dumps(dict(prefs))},
        )

    def app_set_cookies(self, *, cookies: Sequence[Mapping[str, Any]] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/app/setCookies",
            data={
                "cookies": json.dumps(
                    [dict(cookie) for cookie in cookies or ()],
                    separators=(",", ":"),
                )
            },
        )

    def torrents_info(self, **kwargs):
        return self._request_json(
            "GET",
            f"{_API_PREFIX}/torrents/info",
            params=_normalize_info_params(kwargs),
        )

    def torrents_add(
        self,
        *,
        urls: str | Sequence[str] | None = None,
        torrent_files: Mapping[str, Any] | Sequence[Any] | None = None,
        **kwargs,
    ):
        needs_version_shaping = any(
            kwargs.get(key) is not None for key in ("is_paused", "forced", "root_folder")
        )
        parsed = self._parsed_web_api_version() if needs_version_shaping else None
        use_modern_add_payload = parsed is not None and parsed >= _MODERN_ADD_WEB_API_THRESHOLD

        payload_values = {
            "urls": None if urls is None else _coerce_urls(urls, separator="\n"),
            "savepath": kwargs.get("save_path"),
            "category": kwargs.get("category"),
            "tags": (None if kwargs.get("tags") is None else _coerce_tags(kwargs.get("tags"))),
            "skip_checking": kwargs.get("skip_checking"),
            "rename": kwargs.get("rename"),
            "upLimit": kwargs.get("upload_limit"),
            "dlLimit": kwargs.get("download_limit"),
            "autoTMM": kwargs.get("use_auto_torrent_management"),
            "sequentialDownload": kwargs.get("is_sequential_download"),
            "firstLastPiecePrio": kwargs.get("is_first_last_piece_priority"),
            "ratioLimit": kwargs.get("ratio_limit"),
            "seedingTimeLimit": kwargs.get("seeding_time_limit"),
            "inactiveSeedingTimeLimit": kwargs.get("inactive_seeding_time_limit"),
        }
        bool_keys = [
            "skip_checking",
            "autoTMM",
            "sequentialDownload",
            "firstLastPiecePrio",
        ]
        if use_modern_add_payload:
            payload_values["stopped"] = kwargs.get("is_paused")
            payload_values["forced"] = kwargs.get("forced")
            payload_values["contentLayout"] = _coerce_content_layout(kwargs.get("root_folder"))
            bool_keys.extend(["stopped", "forced"])
        else:
            payload_values["paused"] = kwargs.get("is_paused")
            payload_values["root_folder"] = kwargs.get("root_folder")
            bool_keys.extend(["paused", "root_folder"])

        data = _build_payload(payload_values, bool_keys=tuple(bool_keys))

        files = self._normalize_torrent_files(torrent_files)
        return self._request_action(f"{_API_PREFIX}/torrents/add", data=data, files=files)

    def _normalize_torrent_files(
        self,
        torrent_files: Mapping[str, Any] | Sequence[Any] | None,
    ) -> list[tuple[str, Any]] | None:
        if torrent_files is None:
            return None

        normalized: list[tuple[str, Any]] = []

        if isinstance(torrent_files, Mapping):
            for file_name, file_obj in torrent_files.items():
                normalized.append(("torrents", self._normalize_single_file(file_name, file_obj)))
            return normalized

        for index, file_obj in enumerate(torrent_files):
            file_name = getattr(file_obj, "name", None) or f"upload-{index}.torrent"
            normalized.append(("torrents", self._normalize_single_file(file_name, file_obj)))
        return normalized

    def _normalize_single_file(self, file_name: str, file_obj: Any):
        if isinstance(file_obj, bytes):
            return (str(file_name), io.BytesIO(file_obj))
        if isinstance(file_obj, str):
            return (str(file_name), io.BytesIO(file_obj.encode("utf-8")))
        return (str(file_name), file_obj)

    def torrents_files(self, *, torrent_hash: str):
        return self._request_json(
            "GET",
            f"{_API_PREFIX}/torrents/files",
            params={"hash": str(torrent_hash)},
        )

    def torrents_trackers(self, *, torrent_hash: str):
        return self._request_json(
            "GET",
            f"{_API_PREFIX}/torrents/trackers",
            params={"hash": str(torrent_hash)},
        )

    def torrents_properties(self, *, torrent_hash: str):
        return self._request_json(
            "GET",
            f"{_API_PREFIX}/torrents/properties",
            params={"hash": str(torrent_hash)},
        )

    def torrents_delete(
        self, *, torrent_hashes: str | Sequence[str] | None = None, delete_files=False
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/delete",
            data={
                "hashes": _coerce_hashes(torrent_hashes),
                "deleteFiles": _normalize_bool(bool(delete_files)),
            },
        )

    def torrents_recheck(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/recheck",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_reannounce(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/reannounce",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_resume(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/resume",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_pause(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/pause",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_start(self, *, torrent_hashes: str | Sequence[str] | None = None):
        endpoint = self._resolve_lifecycle_endpoint(
            modern_path=f"{_API_PREFIX}/torrents/start",
            legacy_path=f"{_API_PREFIX}/torrents/resume",
        )
        return self._request_action(endpoint, data={"hashes": _coerce_hashes(torrent_hashes)})

    def torrents_stop(self, *, torrent_hashes: str | Sequence[str] | None = None):
        endpoint = self._resolve_lifecycle_endpoint(
            modern_path=f"{_API_PREFIX}/torrents/stop",
            legacy_path=f"{_API_PREFIX}/torrents/pause",
        )
        return self._request_action(endpoint, data={"hashes": _coerce_hashes(torrent_hashes)})

    def torrents_top_priority(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/topPrio",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_bottom_priority(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/bottomPrio",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_increase_priority(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/increasePrio",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_decrease_priority(self, *, torrent_hashes: str | Sequence[str] | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/decreasePrio",
            data={"hashes": _coerce_hashes(torrent_hashes)},
        )

    def torrents_set_upload_limit(
        self,
        *,
        limit: int | None = None,
        torrent_hashes: str | Sequence[str] | None = None,
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/setUploadLimit",
            data={
                "hashes": _coerce_hashes(torrent_hashes),
                "limit": limit if limit is not None else 0,
            },
        )

    def torrents_set_download_limit(
        self,
        *,
        limit: int | None = None,
        torrent_hashes: str | Sequence[str] | None = None,
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/setDownloadLimit",
            data={
                "hashes": _coerce_hashes(torrent_hashes),
                "limit": limit if limit is not None else 0,
            },
        )

    def torrents_file_priority(
        self,
        *,
        torrent_hash: str,
        file_ids: Sequence[int] | None = None,
        priority: int | None = None,
    ):
        file_ids_joined = "|".join(str(file_id) for file_id in file_ids or [])
        return self._request_action(
            f"{_API_PREFIX}/torrents/filePrio",
            data={
                "hash": str(torrent_hash),
                "id": file_ids_joined,
                "priority": priority if priority is not None else 1,
            },
        )

    def torrents_set_location(
        self, *, torrent_hashes: str | Sequence[str] | None = None, location: str
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/setLocation",
            data={"hashes": _coerce_hashes(torrent_hashes), "location": location},
        )

    def torrents_categories(self):
        payload = self._request_json("GET", f"{_API_PREFIX}/torrents/categories")
        if not isinstance(payload, dict):
            raise TransportProtocolError("invalid qBittorrent categories payload")
        return payload

    def torrents_create_category(self, category: str, save_path: str | None = None):
        return self._request_action(
            f"{_API_PREFIX}/torrents/createCategory",
            data={"category": category, "savePath": save_path or ""},
        )

    def torrents_set_category(
        self, *, torrent_hashes: str | Sequence[str] | None = None, category: str
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/setCategory",
            data={"hashes": _coerce_hashes(torrent_hashes), "category": category},
        )

    def torrents_add_tags(
        self, *, torrent_hashes: str | Sequence[str] | None = None, tags: str | Sequence[str]
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/addTags",
            data={"hashes": _coerce_hashes(torrent_hashes), "tags": _coerce_tags(tags)},
        )

    def supports_torrents_set_tags(self) -> bool:
        parsed = self._parsed_web_api_version()
        return parsed is not None and parsed >= _SET_TAGS_WEB_API_THRESHOLD

    def torrents_set_tags(
        self, *, torrent_hashes: str | Sequence[str] | None = None, tags: str | Sequence[str]
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/setTags",
            data={"hashes": _coerce_hashes(torrent_hashes), "tags": _coerce_tags(tags)},
        )

    def torrents_remove_tags(
        self,
        *,
        torrent_hashes: str | Sequence[str] | None = None,
        tags: str | Sequence[str],
    ):
        return self._request_action(
            f"{_API_PREFIX}/torrents/removeTags",
            data={"hashes": _coerce_hashes(torrent_hashes), "tags": _coerce_tags(tags)},
        )

    def torrents_add_trackers(self, *, torrent_hash: str, urls: str | Sequence[str]):
        return self._request_action(
            f"{_API_PREFIX}/torrents/addTrackers",
            data={"hash": str(torrent_hash), "urls": _coerce_urls(urls, separator="\n")},
        )

    def torrents_remove_trackers(self, *, torrent_hash: str, urls: str | Sequence[str]):
        return self._request_action(
            f"{_API_PREFIX}/torrents/removeTrackers",
            data={"hash": str(torrent_hash), "urls": _coerce_urls(urls, separator="|")},
        )

    def torrents_edit_tracker(
        self,
        *,
        torrent_hash: str,
        original_url: str | None = None,
        new_url: str,
        **kwargs,
    ):
        orig_url = original_url or kwargs.get("orig_url")
        if not orig_url:
            raise ValueError("torrents_edit_tracker requires original_url")
        return self._request_action(
            f"{_API_PREFIX}/torrents/editTracker",
            data={"hash": str(torrent_hash), "origUrl": orig_url, "newUrl": new_url},
        )

    def torrents_webseeds(self, *, torrent_hash: str):
        payload = self._request_json(
            "POST",
            f"{_API_PREFIX}/torrents/webseeds",
            data={"hash": str(torrent_hash)},
        )
        if not isinstance(payload, list):
            raise TransportProtocolError("invalid qBittorrent webseeds payload")
        return payload

    def torrents_add_webseeds(self, *, torrent_hash: str, urls: str | Sequence[str]):
        return self._request_action(
            f"{_API_PREFIX}/torrents/addWebSeeds",
            data={"hash": str(torrent_hash), "urls": _coerce_urls(urls, separator="|")},
        )

    def torrents_edit_webseed(
        self,
        *,
        torrent_hash: str,
        original_url: str | None = None,
        new_url: str,
        **kwargs,
    ):
        orig_url = original_url or kwargs.get("orig_url")
        if not orig_url:
            raise ValueError("torrents_edit_webseed requires original_url")
        return self._request_action(
            f"{_API_PREFIX}/torrents/editWebSeed",
            data={"hash": str(torrent_hash), "origUrl": orig_url, "newUrl": new_url},
        )

    def torrents_remove_webseeds(self, *, torrent_hash: str, urls: str | Sequence[str]):
        return self._request_action(
            f"{_API_PREFIX}/torrents/removeWebSeeds",
            data={"hash": str(torrent_hash), "urls": _coerce_urls(urls, separator="|")},
        )

    def torrents_rename(self, *, torrent_hash: str, new_torrent_name: str):
        return self._request_action(
            f"{_API_PREFIX}/torrents/rename",
            data={"hash": str(torrent_hash), "name": new_torrent_name},
        )

    def torrents_rename_file(self, *, torrent_hash: str, old_path: str, new_path: str):
        return self._request_action(
            f"{_API_PREFIX}/torrents/renameFile",
            data={"hash": str(torrent_hash), "oldPath": old_path, "newPath": new_path},
        )

    def transfer_info(self):
        payload = self._request_json("GET", f"{_API_PREFIX}/transfer/info")
        if not isinstance(payload, dict):
            raise TransportProtocolError("invalid qBittorrent transfer info payload")
        return payload

    def transfer_set_upload_limit(self, *, limit: int):
        return self._request_action(
            f"{_API_PREFIX}/transfer/setUploadLimit",
            data={"limit": limit},
        )

    def transfer_set_download_limit(self, *, limit: int):
        return self._request_action(
            f"{_API_PREFIX}/transfer/setDownloadLimit",
            data={"limit": limit},
        )

    def sync_torrent_peers(self, *, torrent_hash: str, rid: int = 0):
        payload = self._request_json(
            "GET",
            f"{_API_PREFIX}/sync/torrentPeers",
            params={"hash": str(torrent_hash), "rid": rid},
        )
        if isinstance(payload, dict):
            return payload
        raise TransportProtocolError("invalid qBittorrent peers payload")
