"""Label Studio REST adapter for the Orchestrator POC."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# Default demo UI: draw weed boxes (RectangleLabels).
# Choices-only triage is still available as CHOICES_LABELING_CONFIG if needed.
LABELING_CONFIG = """
<View>
  <Header value="Draw a box around the weed, then Submit"/>
  <Image name="image" value="$image"/>
  <RectangleLabels name="label" toName="image">
    <Label value="weed" background="#22aa44"/>
    <Label value="unknown" background="#cccc00"/>
    <Label value="unclassifiable" background="#888888"/>
  </RectangleLabels>
</View>
""".strip()

CHOICES_LABELING_CONFIG = """
<View>
  <Header value="Triage — is this a valid weed image?"/>
  <Image name="image" value="$image"/>
  <Choices name="triage" toName="image" choice="single" showInline="true">
    <Choice value="valid_weed"/>
    <Choice value="invalid"/>
    <Choice value="unsure"/>
  </Choices>
</View>
""".strip()

RECTANGLE_LABELING_CONFIG = LABELING_CONFIG

EXPORT_FORMAT = "JSON"
PLATFORM = "label_studio"

# Triage gate: only these LS choices advance the image to CVAT.
VALID_TRIAGE_LABELS = frozenset({"valid_weed", "is_weed", "weed", "valid"})
REJECTED_TRIAGE_LABELS = frozenset({"invalid", "not_weed", "reject", "rejected"})



@dataclass
class LabelStudioConfig:
    base_url: str
    api_token: str
    project_id: int | None = None
    project_title: str = "Abundance Orchestrator POC"
    username: str | None = None
    password: str | None = None
    public_base_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LabelStudioConfig":
        public = data.get("public_base_url")
        project_id = data.get("project_id")
        return cls(
            base_url=str(data.get("base_url") or data.get("ui_url") or "").rstrip("/"),
            api_token=str(data.get("api_token") or ""),
            project_id=int(project_id) if project_id is not None else None,
            project_title=str(data.get("project_title") or "Abundance Orchestrator POC"),
            username=data.get("username"),
            password=data.get("password"),
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


class LabelStudioClient:
    def __init__(self, config: LabelStudioConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self._authed = False
        if config.api_token:
            self.session.headers.update({"Authorization": f"Token {config.api_token}"})

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _ensure_auth(self) -> None:
        if self._authed:
            return
        # Prefer session login — LS 1.23 disables legacy Token auth by default.
        self.signup_or_login()
        self._authed = True

    def ping(self) -> bool:
        try:
            r = requests.get(self._url("/"), timeout=10)
            return r.status_code < 500
        except requests.RequestException:
            return False

    def signup_or_login(self) -> str:
        """Ensure user exists; establish session (+ optional API token string)."""
        username = self.config.username or "admin@local.test"
        password = self.config.password or "SandboxPass123!"

        # If a token already works (legacy enabled), keep it.
        if self.config.api_token:
            self.session.headers["Authorization"] = f"Token {self.config.api_token}"
            probe = self.session.get(self._url("/api/current-user/whoami"), timeout=30)
            if probe.status_code == 200:
                self._authed = True
                return self.config.api_token
            self.session.headers.pop("Authorization", None)

        import re

        signup_page = self.session.get(self._url("/user/signup"), timeout=30)
        csrf = self.session.cookies.get("csrftoken", "")
        m = re.search(
            r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)',
            signup_page.text or "",
        )
        csrf_form = m.group(1) if m else csrf
        self.session.post(
            self._url("/user/signup/"),
            data={
                "csrfmiddlewaretoken": csrf_form,
                "email": username,
                "password1": password,
                "password2": password,
            },
            headers={"Referer": self._url("/user/signup")},
            timeout=60,
            allow_redirects=True,
        )

        login_page = self.session.get(self._url("/user/login"), timeout=30)
        csrf = self.session.cookies.get("csrftoken", "")
        m = re.search(
            r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)',
            login_page.text or "",
        )
        csrf_form = m.group(1) if m else csrf
        self.session.post(
            self._url("/user/login/"),
            data={
                "csrfmiddlewaretoken": csrf_form,
                "email": username,
                "password": password,
            },
            headers={"Referer": self._url("/user/login")},
            timeout=60,
            allow_redirects=True,
        )

        who = self.session.get(self._url("/api/current-user/whoami"), timeout=30)
        if who.status_code != 200:
            raise RuntimeError(
                f"Label Studio session auth failed ({who.status_code}). "
                "Open http://localhost:8081/user/signup once, then re-run bootstrap."
            )

        token_resp = self.session.get(self._url("/api/current-user/token"), timeout=30)
        token = ""
        if token_resp.status_code == 200:
            body = token_resp.json()
            token = body.get("token") or body.get("key") or ""
            # Only attach Token header if legacy auth still works.
            if token:
                probe = requests.get(
                    self._url("/api/projects/"),
                    headers={"Authorization": f"Token {token}"},
                    timeout=30,
                )
                if probe.status_code == 200:
                    self.session.headers["Authorization"] = f"Token {token}"
                    self.config.api_token = token
                else:
                    # Keep password/session auth; store token string for reference only.
                    self.config.api_token = token
                    self.session.headers.pop("Authorization", None)

        self._authed = True
        return self.config.api_token or "session"

    def ensure_project(self) -> int:
        self._ensure_auth()

        if self.config.project_id is not None:
            r = self.session.get(
                self._url(f"/api/projects/{self.config.project_id}/"),
                timeout=30,
            )
            if r.status_code == 200:
                pid = int(self.config.project_id)
                self._ensure_project_settings(pid, current=r.json())
                return pid

        listed = self.session.get(self._url("/api/projects/"), timeout=30)
        listed.raise_for_status()
        results = listed.json().get("results") or listed.json()
        if isinstance(results, list):
            for proj in results:
                if proj.get("title") == self.config.project_title:
                    pid = int(proj["id"])
                    self.config.project_id = pid
                    self._ensure_project_settings(pid, current=proj)
                    return pid

        created = self.session.post(
            self._url("/api/projects/"),
            json={
                "title": self.config.project_title,
                "label_config": LABELING_CONFIG,
                "description": "Orchestrator POC — LS triage (valid_weed → CVAT)",
                # Maps to UI interface flag annotations:deny-empty
                "enable_empty_annotation": False,
            },
            timeout=60,
        )
        created.raise_for_status()
        pid = int(created.json()["id"])
        self.config.project_id = pid
        return pid

    def _ensure_project_settings(
        self,
        project_id: int,
        *,
        current: dict[str, Any] | None = None,
    ) -> None:
        """Deny empty Submit; avoid PATCH loops that fire PROJECT_UPDATED webhooks."""
        self._ensure_auth()
        if current is None:
            r = self.session.get(self._url(f"/api/projects/{project_id}/"), timeout=30)
            r.raise_for_status()
            current = r.json()

        # Only PATCH when needed — demo_state polls list_webhooks often.
        if current.get("enable_empty_annotation") is not False:
            self.session.patch(
                self._url(f"/api/projects/{project_id}/"),
                json={"enable_empty_annotation": False},
                timeout=30,
            ).raise_for_status()

        # Do NOT force LABELING_CONFIG onto an existing project — that replaces
        # RectangleLabels (draw boxes) with Choices (click only) mid-demo.

    def create_task_with_image(
        self,
        *,
        task_name: str,
        image_path: Path,
        upload_filename: str,
    ) -> PushResult:
        self._ensure_auth()
        project_id = self.ensure_project()
        image_path = Path(image_path)
        if not image_path.is_file():
            raise FileNotFoundError(image_path)

        # Import creates a LS task from the uploaded file.
        with image_path.open("rb") as handle:
            imported = self.session.post(
                self._url(f"/api/projects/{project_id}/import"),
                files={"file": (upload_filename, handle, "image/jpeg")},
                timeout=180,
            )
        imported.raise_for_status()
        body = imported.json()
        task_ids = body.get("task_ids") or body.get("tasks") or []
        if not task_ids and isinstance(body.get("id"), int):
            task_ids = [body["id"]]
        if not task_ids:
            # Some LS versions return only counts — list newest task.
            tasks = self.list_project_tasks(project_id)
            if not tasks:
                raise RuntimeError(f"Import succeeded but no task ids returned: {body}")
            task_id = int(tasks[0]["id"])
        else:
            task_id = int(task_ids[0] if not isinstance(task_ids[0], dict) else task_ids[0]["id"])

        # Attach Orchestrator task_ref in meta for round-trip / webhook mapping.
        patch = self.session.patch(
            self._url(f"/api/tasks/{task_id}/"),
            json={
                "meta": {
                    "task_ref": task_name,
                    "citizen_ai_image_id": Path(upload_filename).stem,
                    "upload_filename": upload_filename,
                }
            },
            timeout=30,
        )
        patch.raise_for_status()

        handoff = (
            f"{self.config.browser_base_url}/projects/{project_id}/data?task={task_id}"
        )
        return PushResult(
            external_project_id=str(project_id),
            external_task_id=str(task_id),
            handoff_url=handoff,
        )

    def get_task(self, task_id: str | int) -> dict[str, Any]:
        self._ensure_auth()
        r = self.session.get(self._url(f"/api/tasks/{task_id}/"), timeout=30)
        r.raise_for_status()
        return r.json()

    def list_project_tasks(self, project_id: int | None = None) -> list[dict[str, Any]]:
        self._ensure_auth()
        pid = project_id if project_id is not None else self.ensure_project()
        tasks: list[dict[str, Any]] = []
        page = 1
        while True:
            r = self.session.get(
                self._url("/api/tasks/"),
                params={"project": pid, "page": page, "page_size": 100},
                timeout=60,
            )
            r.raise_for_status()
            body = r.json()
            if isinstance(body, dict):
                batch = body.get("results") or body.get("tasks") or []
            else:
                batch = body
            if not batch:
                break
            tasks.extend(batch)
            if isinstance(body, dict) and not body.get("next") and not (
                body.get("total") and len(tasks) < int(body.get("total") or 0)
            ):
                # paginated via `next` or exhausted total
                if not body.get("next"):
                    break
            page += 1
            if page > 50:
                break
        return tasks

    def export_task_annotations(self, task_id: str | int) -> list[dict[str, Any]]:
        task = self.get_task(task_id)
        return list(task.get("annotations") or [])

    def delete_all_project_tasks(self, project_id: int | None = None) -> dict[str, Any]:
        self._ensure_auth()
        pid = project_id if project_id is not None else self.ensure_project()
        deleted: list[int] = []
        errors: list[dict[str, Any]] = []
        for task in self.list_project_tasks(pid):
            tid = task.get("id")
            try:
                r = self.session.delete(self._url(f"/api/tasks/{tid}/"), timeout=60)
                if r.status_code in (200, 204):
                    deleted.append(int(tid))
                else:
                    errors.append({"task_id": tid, "status": r.status_code, "body": r.text[:200]})
            except Exception as exc:  # noqa: BLE001
                errors.append({"task_id": tid, "error": str(exc)})
        return {
            "deleted_count": len(deleted),
            "deleted_task_ids": deleted,
            "errors": errors,
            "skipped": False,
        }

    def ensure_project_webhook(self, *, target_url: str) -> dict[str, Any]:
        self._ensure_auth()
        project_id = self.ensure_project()
        desired_actions = ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]
        listed = self.session.get(
            self._url("/api/webhooks/"),
            params={"project": project_id},
            timeout=30,
        )
        listed.raise_for_status()
        body = listed.json()
        results = body.get("results") if isinstance(body, dict) else body
        preferred: dict[str, Any] | None = None
        if isinstance(results, list):
            for wh in results:
                url = (wh.get("url") or "").rstrip("/")
                wid = wh.get("id")
                is_orch = ":5050/api/webhooks/label_studio" in url
                if not is_orch:
                    continue
                # Repair legacy send_for_all_actions=true (fires PROJECT_UPDATED in a loop).
                needs = (
                    wh.get("send_for_all_actions") is True
                    or set(wh.get("actions") or []) != set(desired_actions)
                )
                if needs and wid is not None:
                    patched = self.session.patch(
                        self._url(f"/api/webhooks/{wid}/"),
                        json={
                            "send_for_all_actions": False,
                            "send_payload": True,
                            "actions": desired_actions,
                            "is_active": True,
                        },
                        timeout=30,
                    )
                    if patched.status_code < 400:
                        wh = patched.json()
                if url == target_url.rstrip("/"):
                    preferred = wh

        if preferred is not None:
            return preferred

        created = self.session.post(
            self._url("/api/webhooks/"),
            json={
                "project": project_id,
                "url": target_url,
                "send_payload": True,
                "send_for_all_actions": False,
                "actions": desired_actions,
            },
            timeout=60,
        )
        if created.status_code >= 400:
            # Fallback to the minimal action set supported by this LS version.
            created = self.session.post(
                self._url("/api/webhooks/"),
                json={
                    "project": project_id,
                    "url": target_url,
                    "send_payload": True,
                    "send_for_all_actions": False,
                    "actions": ["ANNOTATION_CREATED"],
                },
                timeout=60,
            )
        created.raise_for_status()
        return created.json()

    def list_project_webhooks(self) -> list[dict[str, Any]]:
        """List webhooks without patching project settings (avoids PROJECT_UPDATED loops)."""
        self._ensure_auth()
        pid = self.config.project_id
        if pid is None:
            pid = self.ensure_project()
        r = self.session.get(
            self._url("/api/webhooks/"),
            params={"project": pid},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        if isinstance(body, list):
            return body
        return body.get("results") or []

    def wait_until_ready(self, *, timeout_s: float = 180.0) -> None:
        deadline = time.time() + timeout_s
        last_err = ""
        while time.time() < deadline:
            try:
                r = requests.get(self._url("/"), timeout=5)
                if r.status_code < 500:
                    return
                last_err = f"HTTP {r.status_code}"
            except requests.RequestException as exc:
                last_err = str(exc)
            time.sleep(2)
        raise TimeoutError(f"Label Studio not ready after {timeout_s}s: {last_err}")
