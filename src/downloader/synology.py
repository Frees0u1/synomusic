from __future__ import annotations

import time
import re
from typing import Callable, Optional

import requests

from src.downloader.models import DownloadProgress, DownloadTask


COMPLETE_STATUSES = {"finished", "seeding"}
ERROR_STATUSES = {"error", "broken", "timeout", "extracting_error"}


class SynologyAPIError(RuntimeError):
    pass


DOWNLOAD_STATION_TASK_ERRORS = {
    400: "file upload failed",
    401: "max number of tasks reached",
    402: "destination access denied",
    403: "destination does not exist",
    404: "invalid task id",
    405: "invalid task action",
    406: "no default destination",
    407: "set destination failed",
    408: "file does not exist",
}


class DownloadStationClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        session: requests.Session | None = None,
        timeout: int = 15,
    ):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session = session or requests.Session()
        self._timeout = timeout
        self._sid: str | None = None
        self._api_info: dict[str, dict] = {}

    def login(self) -> None:
        self._load_api_info()
        auth = self._api_entry("SYNO.API.Auth")
        version = min(int(auth.get("maxVersion", 6)), 6)
        data = self._request_path(
            auth["path"],
            {
                "api": "SYNO.API.Auth",
                "version": version,
                "method": "login",
                "account": self._username,
                "passwd": self._password,
                "session": "DownloadStation",
                "format": "sid",
            },
        )
        self._sid = data.get("data", {}).get("sid")
        if not self._sid:
            raise SynologyAPIError("Synology login did not return a sid")

    def logout(self) -> None:
        if not self._sid:
            return
        auth = self._api_entry("SYNO.API.Auth")
        self._request_path(
            auth["path"],
            {
                "api": "SYNO.API.Auth",
                "version": min(int(auth.get("maxVersion", 6)), 6),
                "method": "logout",
                "session": "DownloadStation",
                "_sid": self._sid,
            },
        )
        self._sid = None

    def create_task(self, uri: str, destination: str) -> DownloadTask:
        self._ensure_login()
        api_destination = normalize_download_destination(destination)
        self._request_api(
            "SYNO.DownloadStation.Task",
            "create",
            params={"uri": uri, "destination": api_destination},
            http_method="post",
        )
        task_id = self.find_task_id_by_uri(uri, timeout_seconds=10)
        return DownloadTask(id=task_id, uri=uri, destination=api_destination)

    def list_tasks(self) -> list[dict]:
        self._ensure_login()
        data = self._request_api(
            "SYNO.DownloadStation.Task",
            "list",
            params={"additional": "detail,transfer"},
        )
        return data.get("data", {}).get("tasks", [])

    def get_task(self, task_id: str) -> dict | None:
        self._ensure_login()
        data = self._request_api(
            "SYNO.DownloadStation.Task",
            "getinfo",
            params={"id": task_id, "additional": "detail,transfer"},
        )
        tasks = data.get("data", {}).get("tasks", [])
        return tasks[0] if tasks else None

    def find_task_id_by_uri(self, uri: str, timeout_seconds: int = 0) -> Optional[str]:
        deadline = time.time() + timeout_seconds
        while True:
            for task in self.list_tasks():
                detail = task.get("additional", {}).get("detail", {})
                if detail.get("uri") == uri:
                    return str(task.get("id"))
            if time.time() >= deadline:
                return None
            time.sleep(1)

    def wait_for_completion(
        self,
        task_id: str | None,
        uri: str | None = None,
        poll_interval: int = 15,
        timeout_seconds: int | None = None,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        deadline = time.time() + timeout_seconds if timeout_seconds else None
        last_progress: DownloadProgress | None = None
        while True:
            if task_id:
                task = self.get_task(task_id)
            elif uri:
                found_id = self.find_task_id_by_uri(uri)
                task = self.get_task(found_id) if found_id else None
                task_id = found_id
            else:
                raise ValueError("task_id or uri is required")

            if not task:
                if deadline and time.time() >= deadline:
                    raise SynologyAPIError("Download task not found")
                time.sleep(max(1, poll_interval))
                continue

            last_progress = progress_from_task(task)
            if on_progress:
                on_progress(last_progress)
            if last_progress.status in COMPLETE_STATUSES:
                return last_progress
            if last_progress.status in ERROR_STATUSES:
                raise SynologyAPIError(f"Download task failed: {last_progress.status}")
            if deadline and time.time() >= deadline:
                raise TimeoutError(f"Download task timed out: {last_progress.title}")
            time.sleep(max(1, poll_interval))

    def _ensure_login(self) -> None:
        if not self._sid:
            self.login()

    def _load_api_info(self) -> None:
        if self._api_info:
            return
        data = self._request_path(
            "query.cgi",
            {
                "api": "SYNO.API.Info",
                "version": 1,
                "method": "query",
                "query": "SYNO.API.Auth,SYNO.DownloadStation.Task",
            },
        )
        self._api_info = data.get("data", {})

    def _api_entry(self, api: str) -> dict:
        if api in self._api_info:
            return self._api_info[api]
        defaults = {
            "SYNO.API.Auth": {"path": "auth.cgi", "maxVersion": 6},
            "SYNO.DownloadStation.Task": {"path": "DownloadStation/task.cgi", "maxVersion": 3},
        }
        return defaults[api]

    def _request_api(
        self,
        api: str,
        method: str,
        params: dict | None = None,
        http_method: str = "get",
    ) -> dict:
        entry = self._api_entry(api)
        payload = {
            "api": api,
            "version": int(entry.get("maxVersion", 1)),
            "method": method,
            "_sid": self._sid,
        }
        if params:
            payload.update(params)
        return self._request_path(entry["path"], payload, http_method=http_method)

    def _request_path(self, path: str, params: dict, http_method: str = "get") -> dict:
        url = f"{self._base_url}/webapi/{path.lstrip('/')}"
        if http_method == "post":
            resp = self._session.post(url, data=params, timeout=self._timeout)
        else:
            resp = self._session.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", False):
            code = data.get("error", {}).get("code", "unknown")
            message = _api_error_message(params.get("api"), params.get("method"), code)
            raise SynologyAPIError(
                f"Synology API error {code}: {params.get('api')} {params.get('method')}"
                f"{message}"
            )
        return data


def progress_from_task(task: dict) -> DownloadProgress:
    additional = task.get("additional", {})
    transfer = additional.get("transfer", {})
    size = _to_int(task.get("size")) or _to_int(transfer.get("size"))
    downloaded = _to_int(transfer.get("size_downloaded"))
    return DownloadProgress(
        task_id=str(task.get("id")) if task.get("id") else None,
        title=str(task.get("title") or ""),
        status=str(task.get("status") or ""),
        downloaded_bytes=downloaded,
        size_bytes=size,
        download_speed=_to_int(transfer.get("speed_download")),
    )


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_download_destination(destination: str) -> str:
    value = destination.strip().replace("\\", "/")
    if not value:
        return value
    parts = [part for part in value.split("/") if part]
    if not parts:
        return ""
    first = parts[0].lower()
    if first in {"volumes", "volume"} or re.fullmatch(r"volume(?:\d+|usb\d+|sata\d+)", first):
        parts = parts[1:]
    return "/".join(parts)


def _api_error_message(api: object, method: object, code: object) -> str:
    try:
        numeric_code = int(code)
    except (TypeError, ValueError):
        return ""
    if api == "SYNO.DownloadStation.Task" and method == "create":
        message = DOWNLOAD_STATION_TASK_ERRORS.get(numeric_code)
        return f" ({message})" if message else ""
    return ""
