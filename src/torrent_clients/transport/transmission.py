"""Repository-owned Transmission JSON-RPC transport."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

from torrent_clients.transport.errors import TransportAuthenticationError, TransportProtocolError
from torrent_clients.transport.http import HttpSession

_RPC_PATH = "/transmission/rpc"
_AUTH_FAILURE_STATUSES = {401, 403}
_SESSION_RETRY_LIMIT = 1

_DEFAULT_TORRENT_LIST_FIELDS = (
    "id",
    "hashString",
    "name",
    "downloadDir",
    "percentDone",
    "totalSize",
    "sizeWhenDone",
    "haveValid",
    "labels",
    "status",
    "addedDate",
    "rateDownload",
    "rateUpload",
    "uploadedEver",
    "peersSendingToUs",
    "peersGettingFromUs",
)

_DEFAULT_TORRENT_DETAIL_FIELDS = (
    *_DEFAULT_TORRENT_LIST_FIELDS,
    "comment",
    "files",
    "fileStats",
    "trackerStats",
    "uploadLimit",
    "downloadLimit",
)

_TORRENT_SET_ARG_MAP = {
    "upload_limit": "uploadLimit",
    "download_limit": "downloadLimit",
    "upload_limited": "uploadLimited",
    "download_limited": "downloadLimited",
    "files_wanted": "files-wanted",
    "files_unwanted": "files-unwanted",
    "priority_high": "priority-high",
    "priority_low": "priority-low",
    "priority_normal": "priority-normal",
    "tracker_add": "trackerAdd",
    "tracker_remove": "trackerRemove",
    "tracker_replace": "trackerReplace",
}

_SESSION_SET_ARG_MAP = {
    "speed_limit_down": "speed-limit-down",
    "speed_limit_down_enabled": "speed-limit-down-enabled",
    "speed_limit_up": "speed-limit-up",
    "speed_limit_up_enabled": "speed-limit-up-enabled",
}
_MISSING = object()


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    if len(parts) == 1:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camel_to_snake(value: str) -> str:
    letters: list[str] = []
    for char in value:
        if char.isupper():
            letters.append("_")
            letters.append(char.lower())
        else:
            letters.append(char)
    return "".join(letters).lstrip("_")


class _RpcMapping(dict):
    """Mapping payload that also supports tolerant key/attribute lookups."""

    def _candidate_keys(self, key: str) -> list[str]:
        candidates = [key]

        if "_" in key:
            candidates.append(_snake_to_camel(key))
            candidates.append(key.replace("_", "-"))
        elif "-" in key:
            snake = key.replace("-", "_")
            candidates.append(snake)
            candidates.append(_snake_to_camel(snake))
        elif any(char.isupper() for char in key):
            snake = _camel_to_snake(key)
            candidates.append(snake)
            candidates.append(snake.replace("_", "-"))
        return candidates

    def get(self, key: str, default: Any = None):  # type: ignore[override]
        for candidate in self._candidate_keys(key):
            if dict.__contains__(self, candidate):
                return dict.__getitem__(self, candidate)
        return default

    def __getitem__(self, key: str):  # type: ignore[override]
        value = self.get(key, default=_MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        value = self.get(name, default=_MISSING)
        if value is _MISSING:
            raise AttributeError(name)
        return value


def _wrap_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _RpcMapping({key: _wrap_payload(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_wrap_payload(item) for item in value]
    return value


class TransmissionTransport:
    """Synchronous Transmission RPC transport using repository-owned HTTP primitives."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 15.0,
        session: HttpSession | None = None,
    ) -> None:
        self.timeout = timeout
        self._session = session or HttpSession(url, timeout=timeout)
        self._auth = (username or "", password or "") if (username or password) else None
        self._session_id = ""

    def _rpc_call(
        self,
        method: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> _RpcMapping:
        payload: dict[str, Any] = {"method": method, "arguments": dict(arguments or {})}
        attempts_remaining = _SESSION_RETRY_LIMIT + 1
        request_timeout = self.timeout if timeout is None else timeout

        while attempts_remaining > 0:
            attempts_remaining -= 1
            response = self._session.request(
                "POST",
                _RPC_PATH,
                headers={"X-Transmission-Session-Id": self._session_id},
                json=payload,
                auth=self._auth,
                timeout=request_timeout,
            )

            if response.status_code == 409:
                next_session_id = response.headers.get("X-Transmission-Session-Id", "")
                if not next_session_id:
                    raise TransportProtocolError(
                        "missing X-Transmission-Session-Id header in 409 response"
                    )
                self._session_id = next_session_id
                continue

            if response.status_code in _AUTH_FAILURE_STATUSES:
                raise TransportAuthenticationError(
                    f"Transmission authentication failed with HTTP {response.status_code}"
                )

            if response.status_code != 200:
                raise TransportProtocolError(
                    f"Transmission RPC HTTP {response.status_code} for method '{method}'"
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise TransportProtocolError(
                    f"invalid JSON response from Transmission RPC method '{method}'"
                ) from exc

            if not isinstance(body, Mapping):
                raise TransportProtocolError(
                    f"invalid Transmission RPC payload for method '{method}'"
                )

            result = body.get("result")
            if result != "success":
                message = str(result) if result else "unknown Transmission RPC failure"
                raise TransportProtocolError(message)

            arguments_payload = body.get("arguments", {})
            if arguments_payload is None:
                arguments_payload = {}
            if not isinstance(arguments_payload, Mapping):
                raise TransportProtocolError(
                    f"invalid Transmission RPC arguments payload for method '{method}'"
                )
            return _wrap_payload(dict(arguments_payload))

        raise TransportProtocolError(
            f"Transmission RPC session id was rejected after retry for method '{method}'"
        )

    @staticmethod
    def _normalize_torrent_ids(torrent_ids: Any) -> list[Any]:
        if isinstance(torrent_ids, Sequence) and not isinstance(
            torrent_ids, (str, bytes, bytearray)
        ):
            return list(torrent_ids)
        return [torrent_ids]

    def add_torrent(
        self,
        torrent_input: str | bytes,
        *,
        download_dir: str | None = None,
        paused: bool = True,
    ) -> _RpcMapping:
        arguments: dict[str, Any] = {"paused": paused}

        if isinstance(torrent_input, (bytes, bytearray)):
            arguments["metainfo"] = base64.b64encode(bytes(torrent_input)).decode("ascii")
        else:
            arguments["filename"] = torrent_input

        if download_dir is not None:
            arguments["download-dir"] = download_dir

        response = self._rpc_call("torrent-add", arguments)
        payload = response.get("torrent-added") or response.get("torrent-duplicate")
        if not isinstance(payload, Mapping):
            raise TransportProtocolError("torrent-add response missing torrent payload")
        return _wrap_payload(dict(payload))

    def change_torrent(self, torrent_ids: Any, **kwargs: Any) -> None:
        arguments: dict[str, Any] = {"ids": self._normalize_torrent_ids(torrent_ids)}
        for key, value in kwargs.items():
            rpc_key = _TORRENT_SET_ARG_MAP.get(key, key.replace("_", "-"))
            arguments[rpc_key] = value
        self._rpc_call("torrent-set", arguments)

    def get_torrent(
        self, torrent_id: Any, arguments: list[str] | None = None
    ) -> _RpcMapping | None:
        fields = list(arguments) if arguments is not None else list(_DEFAULT_TORRENT_DETAIL_FIELDS)
        torrents = self.get_torrents(ids=[torrent_id], arguments=fields)
        if not torrents:
            return None
        return torrents[0]

    def get_torrents(
        self,
        ids: Sequence[Any] | None = None,
        arguments: list[str] | None = None,
    ) -> list[_RpcMapping]:
        fields = list(arguments) if arguments is not None else list(_DEFAULT_TORRENT_LIST_FIELDS)
        if "id" not in fields:
            fields.append("id")

        rpc_arguments: dict[str, Any] = {"fields": fields}
        if ids is not None:
            rpc_arguments["ids"] = self._normalize_torrent_ids(ids)

        response = self._rpc_call("torrent-get", rpc_arguments)
        torrents_payload = response.get("torrents", [])
        if not isinstance(torrents_payload, list):
            raise TransportProtocolError("torrent-get response 'torrents' must be a list")

        torrents: list[_RpcMapping] = []
        for item in torrents_payload:
            if not isinstance(item, Mapping):
                raise TransportProtocolError("torrent-get response item must be a mapping")
            wrapped_item = _wrap_payload(dict(item))
            wrapped_item.fields = set(fields)  # type: ignore[attr-defined]
            torrents.append(wrapped_item)
        return torrents

    def remove_torrent(self, torrent_ids: Any, *, delete_data: bool = False) -> None:
        self._rpc_call(
            "torrent-remove",
            {
                "ids": self._normalize_torrent_ids(torrent_ids),
                "delete-local-data": delete_data,
            },
        )

    def move_torrent_data(
        self,
        torrent_ids: Any,
        *,
        location: str,
        move: bool = True,
        timeout: float | None = None,
    ) -> None:
        self._rpc_call(
            "torrent-set-location",
            {
                "ids": self._normalize_torrent_ids(torrent_ids),
                "location": location,
                "move": move,
            },
            timeout=timeout,
        )

    def rename_torrent_path(self, torrent_id: Any, old_path: str, new_path: str) -> _RpcMapping:
        return self._rpc_call(
            "torrent-rename-path",
            {
                "ids": torrent_id,
                "path": old_path,
                "name": new_path,
            },
        )

    def start_torrent(self, torrent_ids: Any) -> None:
        self._rpc_call("torrent-start", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def stop_torrent(self, torrent_ids: Any) -> None:
        self._rpc_call("torrent-stop", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def verify_torrent(self, torrent_ids: Any) -> None:
        self._rpc_call("torrent-verify", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def reannounce_torrent(self, torrent_ids: Any) -> None:
        self._rpc_call("torrent-reannounce", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def queue_up(self, torrent_ids: Any) -> None:
        self._rpc_call("queue-move-up", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def queue_down(self, torrent_ids: Any) -> None:
        self._rpc_call("queue-move-down", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def queue_top(self, torrent_ids: Any) -> None:
        self._rpc_call("queue-move-top", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def queue_bottom(self, torrent_ids: Any) -> None:
        self._rpc_call("queue-move-bottom", {"ids": self._normalize_torrent_ids(torrent_ids)})

    def set_session(self, **kwargs: Any) -> None:
        arguments = {
            _SESSION_SET_ARG_MAP.get(key, key.replace("_", "-")): value
            for key, value in kwargs.items()
        }
        self._rpc_call("session-set", arguments)

    def get_session(self) -> _RpcMapping:
        return self._rpc_call("session-get")

    def session_stats(self) -> _RpcMapping:
        return self._rpc_call("session-stats")
