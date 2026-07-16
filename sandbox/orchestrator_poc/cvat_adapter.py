"""CVAT Community REST adapter for the Orchestrator POC."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

DEFAULT_LABELS = [
    {"name": "weed", "color": "#22aa44", "attributes": []},
    {"name": "unknown", "color": "#cccc00", "attributes": []},
    {"name": "unclassifiable", "color": "#888888", "attributes": []},
]

EXPORT_FORMAT = "COCO 1.0"


@dataclass
class CvatConfig:
    base_url: str
    username: str
    password: str
    project_id: int
    public_base_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CvatConfig":
        base_url = data.get("base_url") or data.get("ui_url", "").rsplit("/tasks", 1)[0]
        public = data.get("public_base_url")
        return cls(
            base_url=base_url.rstrip("/"),
            username=data["username"],
            password=data["password"],
            project_id=int(data["project_id"]),
            public_base_url=(public.rstrip("/") if public else None),
        )

    @property
    def browser_base_url(self) -> str:
        return self.public_base_url or self.base_url


@dataclass
class PushResult:
    external_project_id: str
    external_task_id: str
    handoff_url: str


class CvatClient:
    def __init__(self, config: CvatConfig) -> None:
        self.config = config
        self.session = requests.Session()
        # Django ALLOWED_HOSTS: when calling cvat_server by Docker DNS, spoof Host.
        public_host = (config.public_base_url or config.base_url).split("://", 1)[-1].split("/")[0]
        self.session.headers.update({"Host": public_host.split(":")[0] or "localhost"})

    def login(self) -> None:
        probe = self.session.get(f"{self.config.base_url}/api/users/self", timeout=30)
        if probe.status_code == 200:
            return
        response = self.session.post(
            f"{self.config.base_url}/api/auth/login",
            json={
                "username": self.config.username,
                "password": self.config.password,
            },
            timeout=30,
        )
        response.raise_for_status()

    @property
    def _csrf_headers(self) -> dict[str, str]:
        csrf = self.session.cookies.get("csrftoken", "")
        referer = self.config.public_base_url or self.config.base_url
        return {
            "X-CSRFToken": csrf,
            "Referer": referer,
        }

    def create_task_with_image(
        self,
        *,
        task_name: str,
        image_path: Path,
        upload_filename: str,
    ) -> PushResult:
        self.login()

        create_task = self.session.post(
            f"{self.config.base_url}/api/tasks",
            json={
                "name": task_name,
                "project_id": self.config.project_id,
            },
            headers={**self._csrf_headers, "Content-Type": "application/json"},
            timeout=60,
        )
        create_task.raise_for_status()
        task_id = create_task.json()["id"]

        image_bytes = image_path.read_bytes()
        form_data = {
            "image_quality": "70",
            "use_zip_chunks": "true",
            "use_cache": "true",
            "storage": "local",
            "sorting_method": "natural",
        }
        files = [("client_files[0]", (upload_filename, image_bytes, "image/jpeg"))]

        upload = self.session.post(
            f"{self.config.base_url}/api/tasks/{task_id}/data",
            headers={**self._csrf_headers, "Upload-Start": "true", "Upload-Finish": "true"},
            data=form_data,
            files=files,
            timeout=180,
        )
        upload.raise_for_status()
        rq_id = upload.json().get("rq_id")
        if rq_id:
            self.wait_for_request(rq_id)

        self._wait_for_task_ready(task_id)
        handoff_url = f"{self.config.browser_base_url}/tasks/{task_id}"
        return PushResult(
            external_project_id=str(self.config.project_id),
            external_task_id=str(task_id),
            handoff_url=handoff_url,
        )

    def export_task_coco(self, external_task_id: str) -> bytes:
        self.login()
        response = self.session.post(
            f"{self.config.base_url}/api/tasks/{external_task_id}/dataset/export",
            params={
                "format": EXPORT_FORMAT,
                "save_images": "false",
            },
            headers=self._csrf_headers,
            timeout=60,
        )
        response.raise_for_status()
        rq_id = response.json()["rq_id"]
        request_data = self.wait_for_request(rq_id)
        result_url = request_data.get("result_url")
        if not result_url:
            raise RuntimeError(f"CVAT export finished without result_url: {request_data}")
        # CVAT may return localhost URLs; rewrite to the API base reachable from this process.
        if "://" in result_url:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(result_url)
            base = urlparse(self.config.base_url)
            result_url = urlunparse(
                (base.scheme, base.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
        download = self.session.get(result_url, timeout=120)
        download.raise_for_status()
        return download.content

    def get_task(self, external_task_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.config.base_url}/api/tasks/{external_task_id}",
            timeout=30,
        )
        if response.status_code == 401:
            self.login()
            response = self.session.get(
                f"{self.config.base_url}/api/tasks/{external_task_id}",
                timeout=30,
            )
        response.raise_for_status()
        return response.json()

    def list_jobs_for_task(self, external_task_id: str) -> list[dict[str, Any]]:
        self.login()
        response = self.session.get(
            f"{self.config.base_url}/api/jobs",
            params={"task_id": external_task_id, "page_size": 100},
            headers=self._csrf_headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("results", [])

    def list_project_tasks(self) -> list[dict[str, Any]]:
        self.login()
        tasks: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.session.get(
                f"{self.config.base_url}/api/tasks",
                params={
                    "project_id": self.config.project_id,
                    "page_size": 100,
                    "page": page,
                },
                headers=self._csrf_headers,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            tasks.extend(data.get("results", []))
            if not data.get("next"):
                break
            page += 1
        return tasks

    def delete_task(self, external_task_id: str | int) -> None:
        self.login()
        response = self.session.delete(
            f"{self.config.base_url}/api/tasks/{external_task_id}",
            headers=self._csrf_headers,
            timeout=60,
        )
        if response.status_code in (204, 404):
            return
        response.raise_for_status()

    def delete_all_project_tasks(self) -> dict[str, Any]:
        """Delete every task in the configured CVAT project (sandbox reset)."""
        tasks = self.list_project_tasks()
        deleted: list[int] = []
        errors: list[dict[str, Any]] = []
        for task in tasks:
            tid = task.get("id")
            try:
                self.delete_task(tid)
                deleted.append(int(tid))
            except Exception as exc:  # noqa: BLE001
                errors.append({"task_id": tid, "error": str(exc)})
        return {
            "project_id": self.config.project_id,
            "deleted_count": len(deleted),
            "deleted_task_ids": deleted,
            "errors": errors,
        }

    def list_project_webhooks(self) -> list[dict[str, Any]]:
        self.login()
        response = self.session.get(
            f"{self.config.base_url}/api/webhooks",
            params={"project_id": self.config.project_id, "page_size": 100},
            headers=self._csrf_headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("results", [])

    def ensure_project_webhook(
        self,
        *,
        target_url: str,
        description: str = "Orchestrator POC — pull on job completed",
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create project webhook for update:job, or return existing matching target_url."""
        self.login()
        events = events or ["update:job"]
        existing = [
            w
            for w in self.list_project_webhooks()
            if w.get("target_url") == target_url and w.get("is_active")
        ]
        if existing:
            return existing[0]

        response = self.session.post(
            f"{self.config.base_url}/api/webhooks",
            json={
                "target_url": target_url,
                "description": description,
                "type": "project",
                "project_id": self.config.project_id,
                "events": events,
                "content_type": "application/json",
                "is_active": True,
                "enable_ssl": False,
            },
            headers={**self._csrf_headers, "Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def wait_for_request(
        self,
        rq_id: str,
        *,
        timeout_seconds: int = 300,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            response = self.session.get(
                f"{self.config.base_url}/api/requests/{rq_id}",
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "finished":
                return data
            if status == "failed":
                raise RuntimeError(f"CVAT request failed: {data.get('message')}")
            time.sleep(poll_seconds)
        raise TimeoutError(f"Timed out waiting for CVAT request {rq_id}")

    def _wait_for_task_ready(
        self,
        task_id: int,
        *,
        timeout_seconds: int = 300,
        poll_seconds: float = 3.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            task = self.get_task(str(task_id))
            if task.get("size", 0) >= 1 and task.get("jobs", {}).get("count", 0) >= 1:
                return
            time.sleep(poll_seconds)
        raise TimeoutError(f"Timed out waiting for CVAT task {task_id} to become ready")
