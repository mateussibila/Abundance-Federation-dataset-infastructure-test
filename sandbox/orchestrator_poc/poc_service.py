"""Shared Orchestrator CVAT POC service layer (CLI + demo UI)."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cvat_adapter import CvatClient, CvatConfig, EXPORT_FORMAT
from db import Database
from normalize_coco import extract_json_bytes_from_zip, normalize_coco_for_image, save_normalized_payload

POC_DIR = Path(__file__).resolve().parent
DEFAULT_DB = POC_DIR / "poc.db"
DEFAULT_CONFIG = Path(
    os.environ.get("CVAT_CONFIG") or (POC_DIR.parent / "cvat-sandbox-info.json")
)
RAW_EXPORT_DIR = POC_DIR / "exports" / "raw"
NORMALIZED_EXPORT_DIR = POC_DIR / "exports" / "normalized"

DEMO_IMAGE_ID = "CAI-WP01-IMG-000001"
DEMO_IMAGE_PATH = Path(
    os.environ.get("DEMO_IMAGE_PATH")
    or (POC_DIR.parent / "cvat-dummy-images" / "sandbox_farmA_vol001_20250713_topdown_01.jpg")
)
# Fallback only; demo UI uses next_demo_task_ref() / active_demo_task_ref().
DEMO_TASK_REF = "TASK-0001"
DEMO_METADATA = {"farm": "farmA", "volunteer_id": "vol001", "photo_type": "topdown"}
_TASK_REF_RE = re.compile(r"^TASK-(\d+)$", re.IGNORECASE)

# Prefer in-network URL when Orchestrator runs beside CVAT (Docker).
# host.docker.internal is blocked by Docker Desktop egress "Deny: Private Range".
DEFAULT_WEBHOOK_TARGET = os.environ.get(
    "WEBHOOK_TARGET_URL",
    "http://orchestrator_poc:5050/api/webhooks/cvat",
)

# In-memory feed for the demo UI (newest last). Not persisted.
_WEBHOOK_FEED: list[dict[str, Any]] = []
_WEBHOOK_FEED_MAX = 50
# job_ids already auto-pulled (or claimed) while state=completed
_COMPLETED_PULLS: set[str] = set()
_PULL_LOCK = threading.Lock()


@dataclass
class PocSettings:
    db_path: Path = DEFAULT_DB
    config_path: Path = DEFAULT_CONFIG


class PocError(Exception):
    pass


def load_config(config_path: Path) -> CvatConfig:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    # Inside Docker, talk to CVAT by service name; browser links stay on localhost.
    api_base = os.environ.get("CVAT_API_BASE_URL")
    public_base = os.environ.get("CVAT_PUBLIC_BASE_URL")
    if api_base:
        data = {**data, "base_url": api_base.rstrip("/")}
    if public_base:
        data = {**data, "public_base_url": public_base.rstrip("/")}
    return CvatConfig.from_dict(data)


def _db(settings: PocSettings) -> Database:
    return Database(settings.db_path)


def next_demo_task_ref(settings: PocSettings | None = None) -> str:
    """Next TASK-NNNN based on local DB + existing CVAT project task names."""
    settings = settings or PocSettings()
    nums: list[int] = []
    db = _db(settings)
    db.init_db()
    with db.connect() as conn:
        for row in conn.execute("SELECT task_ref FROM tasks").fetchall():
            match = _TASK_REF_RE.match(row["task_ref"] or "")
            if match:
                nums.append(int(match.group(1)))
    try:
        client = CvatClient(load_config(settings.config_path))
        for task in client.list_project_tasks():
            match = _TASK_REF_RE.match(task.get("name") or "")
            if match:
                nums.append(int(match.group(1)))
    except Exception:  # noqa: BLE001 - offline CVAT should not block local demo
        pass
    return f"TASK-{(max(nums) if nums else 0) + 1:04d}"


def active_demo_task_ref(settings: PocSettings | None = None) -> str:
    """Most recent local task_ref, or the next ref if none exist yet."""
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT task_ref FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row:
        return row["task_ref"]
    return next_demo_task_ref(settings)


def init_poc(settings: PocSettings | None = None) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    RAW_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return {"db_path": str(settings.db_path)}


def seed_image(
    *,
    image_id: str,
    path: str | Path,
    metadata: dict[str, Any] | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    image_path = Path(path).resolve()
    if not image_path.is_file():
        raise PocError(f"Image not found: {image_path}")

    meta = metadata or {}
    row_id = db.seed_image(image_id, str(image_path), meta)
    return {
        "image_id": image_id,
        "orchestrator_image_id": row_id,
        "storage_path": str(image_path),
        "metadata": meta,
        "workflow_state": "validated",
    }


def push_to_cvat(
    *,
    image_id: str,
    task_ref: str,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    image = db.get_image_by_citizen_id(image_id)
    if image is None:
        raise PocError(f"Unknown image id: {image_id}. Run seed-image first.")

    existing = db.get_task_by_ref(task_ref)
    if existing is not None:
        raise PocError(
            f"Task ref already exists: {task_ref} -> CVAT task {existing['external_task_id']}"
        )

    image_path = Path(image["storage_path"])
    if not image_path.is_file():
        raise PocError(f"Image file missing: {image_path}")

    config = load_config(settings.config_path)
    client = CvatClient(config)
    upload_name = f"{image_id}.jpg"
    result = client.create_task_with_image(
        task_name=task_ref,
        image_path=image_path,
        upload_filename=upload_name,
    )

    task_id = db.create_task(
        task_ref=task_ref,
        external_project_id=result.external_project_id,
        external_task_id=result.external_task_id,
        handoff_url=result.handoff_url,
    )
    db.link_task_image(task_id, image["id"])
    db.update_image_workflow_state(image["id"], "queued_for_annotation")

    return {
        "image_id": image_id,
        "task_ref": task_ref,
        "orchestrator_task_id": task_id,
        "external_task_id": result.external_task_id,
        "handoff_url": result.handoff_url,
        "upload_filename": upload_name,
    }


def pull_from_cvat(
    *,
    task_ref: str,
    force: bool = False,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    task = db.get_task_by_ref(task_ref)
    if task is None:
        raise PocError(f"Unknown task ref: {task_ref}")

    images = db.get_task_images(task["id"])
    if not images:
        raise PocError(f"No images linked to task {task_ref}")

    if force:
        db.clear_active_annotations(task["id"])
        for image in images:
            db.update_image_workflow_state(image["id"], "queued_for_annotation")
        db.update_task_status(task["id"], "queued_for_annotation")

    config = load_config(settings.config_path)
    client = CvatClient(config)
    export_bytes = client.export_task_coco(task["external_task_id"])

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = RAW_EXPORT_DIR / f"{task_ref}_{timestamp}.zip"
    raw_path.write_bytes(export_bytes)

    _, coco = extract_json_bytes_from_zip(export_bytes)
    results: list[dict[str, Any]] = []
    created = 0
    skipped = 0

    for image in images:
        if db.get_active_annotation(task["id"], image["id"]) is not None:
            skipped += 1
            results.append(
                {
                    "citizen_ai_image_id": image["citizen_ai_image_id"],
                    "skipped": True,
                    "labels_count": 0,
                }
            )
            continue

        payload = normalize_coco_for_image(
            coco,
            image_ref=image["citizen_ai_image_id"],
            external_task_id=task["external_task_id"],
            export_format=EXPORT_FORMAT,
        )
        normalized_path = NORMALIZED_EXPORT_DIR / f"{task_ref}_{image['citizen_ai_image_id']}.json"
        save_normalized_payload(payload, normalized_path)

        annotation_id = db.create_annotation(
            task_id=task["id"],
            image_id=image["id"],
            payload=payload,
            export_format=EXPORT_FORMAT,
            raw_export_path=str(raw_path),
        )
        db.update_image_workflow_state(image["id"], "annotated")
        created += 1
        results.append(
            {
                "citizen_ai_image_id": image["citizen_ai_image_id"],
                "annotation_id": annotation_id,
                "labels_count": len(payload["labels"]),
                "payload": payload,
                "normalized_path": str(normalized_path),
            }
        )

    db.update_task_status(task["id"], "annotated")
    return {
        "task_ref": task_ref,
        "created": created,
        "skipped": skipped,
        "raw_export_path": str(raw_path),
        "images": results,
        "total_labels": sum(item.get("labels_count", 0) for item in results),
    }


def get_status(
    *,
    task_ref: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    report = db.status_report(task_ref)
    if not report:
        return {"tasks": []}

    config = load_config(settings.config_path)
    client = CvatClient(config)

    tasks: list[dict[str, Any]] = []
    for item in report:
        task = item["task"]
        entry: dict[str, Any] = {
            "task": task,
            "images": item["images"],
            "annotations": [],
            "cvat_live": None,
        }
        for ann in item["annotations"]:
            payload = json.loads(ann["payload_json"])
            entry["annotations"].append(
                {
                    **ann,
                    "labels_count": len(payload.get("labels", [])),
                    "payload": payload,
                }
            )
        if task.get("external_task_id"):
            try:
                cvat_task = client.get_task(task["external_task_id"])
                entry["cvat_live"] = {
                    "size": cvat_task.get("size"),
                    "jobs": cvat_task.get("jobs", {}).get("count"),
                }
            except Exception as exc:  # noqa: BLE001 - demo status helper
                entry["cvat_live"] = {"error": str(exc)}
        tasks.append(entry)

    return {"tasks": tasks}


def get_entities(settings: PocSettings | None = None) -> dict[str, list[dict[str, Any]]]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    return db.list_all_entities()


def reset_all(settings: PocSettings | None = None) -> dict[str, Any]:
    """Clear Orchestrator SQLite and delete all tasks in the CVAT sandbox project."""
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()

    cvat_result: dict[str, Any] = {
        "deleted_count": 0,
        "deleted_task_ids": [],
        "errors": [],
        "skipped": False,
    }
    try:
        config = load_config(settings.config_path)
        client = CvatClient(config)
        cvat_result = client.delete_all_project_tasks()
    except Exception as exc:  # noqa: BLE001
        cvat_result = {
            "deleted_count": 0,
            "deleted_task_ids": [],
            "errors": [{"error": str(exc)}],
            "skipped": False,
        }

    db.clear_all_records()
    _WEBHOOK_FEED.clear()
    _COMPLETED_PULLS.clear()

    return {
        "cleared": True,
        "cvat": cvat_result,
    }


def _append_webhook_feed(entry: dict[str, Any]) -> None:
    _WEBHOOK_FEED.append(entry)
    if len(_WEBHOOK_FEED) > _WEBHOOK_FEED_MAX:
        del _WEBHOOK_FEED[: len(_WEBHOOK_FEED) - _WEBHOOK_FEED_MAX]


def get_webhook_feed(*, after_id: int = 0) -> list[dict[str, Any]]:
    return [e for e in _WEBHOOK_FEED if int(e.get("id", 0)) > after_id]


def resolve_webhook_target(preferred: str | None = None) -> str:
    """
    CVAT URL validator rejects bare Docker DNS names (orchestrator_poc).
    Resolve to an IP on the shared network when needed.
    """
    preferred = preferred or DEFAULT_WEBHOOK_TARGET
    from urllib.parse import urlparse, urlunparse
    import socket

    parsed = urlparse(preferred)
    host = parsed.hostname or ""
    if not host or host.replace(".", "").isdigit():
        return preferred
    if "." in host and host not in {"host.docker.internal"}:
        return preferred
    try:
        ip = socket.gethostbyname(host)
        netloc = f"{ip}:{parsed.port}" if parsed.port else ip
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except OSError:
        return preferred


def register_cvat_webhook(
    *,
    target_url: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    config = load_config(settings.config_path)
    client = CvatClient(config)
    resolved = resolve_webhook_target(target_url or DEFAULT_WEBHOOK_TARGET)
    webhook = client.ensure_project_webhook(target_url=resolved)
    return {
        "webhook_id": webhook.get("id"),
        "target_url": webhook.get("target_url"),
        "requested_target_url": target_url or DEFAULT_WEBHOOK_TARGET,
        "events": webhook.get("events"),
        "project_id": webhook.get("project_id"),
        "is_active": webhook.get("is_active"),
        "description": webhook.get("description"),
    }


def list_cvat_webhooks(settings: PocSettings | None = None) -> dict[str, Any]:
    settings = settings or PocSettings()
    config = load_config(settings.config_path)
    client = CvatClient(config)
    webhooks = client.list_project_webhooks()
    return {"webhooks": webhooks, "count": len(webhooks)}


def _job_became_completed(payload: dict[str, Any]) -> bool:
    """True when update:job moves state to completed (production pull trigger)."""
    event = payload.get("event") or payload.get("type") or ""
    if event != "update:job":
        return False
    job = payload.get("job") or {}
    state = (job.get("state") or "").strip().lower()
    if state != "completed":
        return False
    before = payload.get("before_update") or {}
    if "state" in before:
        return (before.get("state") or "").strip().lower() != "completed"
    # Some deliveries omit before_update.state — still treat completed as actionable.
    return True


def handle_cvat_webhook(
    payload: dict[str, Any],
    *,
    settings: PocSettings | None = None,
    force_pull: bool = True,
) -> dict[str, Any]:
    """Process CVAT webhook. Auto-pull only when job state → completed."""
    settings = settings or PocSettings()
    event = payload.get("event") or payload.get("type") or "unknown"
    job = payload.get("job") or {}
    task_id_cvat = job.get("task_id")
    job_id = job.get("id")
    state = job.get("state")

    feed_id = len(_WEBHOOK_FEED) + 1
    base_feed = {
        "id": feed_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "job_id": job_id,
        "cvat_task_id": task_id_cvat,
        "job_state": state,
    }

    if not _job_became_completed(payload):
        # If job left completed, allow a future completed→pull cycle.
        if job_id is not None and (state or "").strip().lower() != "completed":
            with _PULL_LOCK:
                _COMPLETED_PULLS.discard(str(job_id))
        result = {
            **base_feed,
            "action": "ignored",
            "reason": "Not a job→completed transition (Save alone does not pull)",
        }
        _append_webhook_feed(result)
        return result

    # Claim under lock BEFORE slow export — stops overlapping poller ticks.
    if job_id is not None:
        with _PULL_LOCK:
            if str(job_id) in _COMPLETED_PULLS:
                return {
                    **base_feed,
                    "action": "ignored",
                    "reason": (
                        f"Job #{job_id} already auto-pulled while completed "
                        "(no re-pull until state changes)"
                    ),
                }
            _COMPLETED_PULLS.add(str(job_id))

    if task_id_cvat is None:
        if job_id is not None:
            with _PULL_LOCK:
                _COMPLETED_PULLS.discard(str(job_id))
        result = {
            **base_feed,
            "action": "error",
            "reason": "Webhook job payload missing task_id",
        }
        _append_webhook_feed(result)
        return result

    db = _db(settings)
    db.init_db()
    task = db.get_task_by_external_id(str(task_id_cvat))
    if task is None:
        if job_id is not None:
            with _PULL_LOCK:
                _COMPLETED_PULLS.discard(str(job_id))
        result = {
            **base_feed,
            "action": "ignored",
            "reason": f"No Orchestrator task mapped to CVAT task #{task_id_cvat}",
        }
        _append_webhook_feed(result)
        return result

    try:
        pull = pull_from_cvat(
            task_ref=task["task_ref"],
            force=force_pull,
            settings=settings,
        )
    except Exception:
        if job_id is not None:
            with _PULL_LOCK:
                _COMPLETED_PULLS.discard(str(job_id))
        raise

    result = {
        **base_feed,
        "action": "auto_pull",
        "task_ref": task["task_ref"],
        "source": payload.get("source", "webhook"),
        "pull": {
            "created": pull.get("created"),
            "skipped": pull.get("skipped"),
            "total_labels": pull.get("total_labels"),
            "images": [
                {
                    "citizen_ai_image_id": img.get("citizen_ai_image_id"),
                    "labels_count": img.get("labels_count", 0),
                    "skipped": img.get("skipped", False),
                }
                for img in pull.get("images", [])
            ],
        },
    }
    _append_webhook_feed(result)
    return result


def poll_completed_jobs(
    *,
    settings: PocSettings | None = None,
    force_pull: bool = False,
) -> list[dict[str, Any]]:
    """
    Mac/Docker Desktop fallback: CVAT webhook egress to private IPs is blocked
    ('Deny: Private Range'). Orchestrator polls CVAT for job state=completed
    and runs the same auto-pull path as the webhook handler.

    Default force_pull=False: if annotations already exist, skip (no loop).
    """
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    config = load_config(settings.config_path)
    client = CvatClient(config)

    results: list[dict[str, Any]] = []
    with db.connect() as conn:
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]

    for task in tasks:
        ext = task.get("external_task_id")
        if not ext:
            continue
        try:
            jobs = client.list_jobs_for_task(str(ext))
        except Exception as exc:  # noqa: BLE001
            results.append({"action": "error", "reason": str(exc), "cvat_task_id": ext})
            continue

        for job in jobs:
            job_id = str(job.get("id"))
            state = (job.get("state") or "").strip().lower()
            if state != "completed":
                with _PULL_LOCK:
                    _COMPLETED_PULLS.discard(job_id)
                continue

            with _PULL_LOCK:
                already = job_id in _COMPLETED_PULLS
            if already:
                continue

            # Already imported for this Orchestrator task → mark done, no re-pull.
            images = db.get_task_images(task["id"])
            if images and all(
                db.get_active_annotation(task["id"], img["id"]) is not None for img in images
            ):
                with _PULL_LOCK:
                    _COMPLETED_PULLS.add(job_id)
                continue

            synthetic = {
                "event": "update:job",
                "job": job,
                "before_update": {"state": "in progress"},
                "source": "poller",
            }
            try:
                pull_result = handle_cvat_webhook(
                    synthetic, settings=settings, force_pull=force_pull
                )
            except Exception as exc:  # noqa: BLE001
                results.append({"action": "error", "reason": str(exc), "job_id": job_id})
                continue
            if pull_result.get("action") == "auto_pull":
                results.append(pull_result)

    return results


def entity_changes_for_action(action: str, data: dict[str, Any]) -> list[dict[str, str]]:
    """Human-readable entity change log entries for the demo UI."""
    changes: list[dict[str, str]] = []

    def entry(entity: str, summary: str, detail: str) -> dict[str, str]:
        return {"entity": entity, "summary": summary, "detail": detail}

    if action == "init":
        changes.append(
            entry(
                "Database",
                "SQLite schema ready (POC setup only)",
                "Created local tables: images, tasks, task_images, annotations. "
                "In production this happens once at deploy via Django migrations — not a user action.",
            )
        )
    elif action == "reset":
        cvat = data.get("cvat") or {}
        deleted = cvat.get("deleted_count", 0)
        ids = cvat.get("deleted_task_ids") or []
        err_n = len(cvat.get("errors") or [])
        id_preview = ", ".join(f"#{i}" for i in ids[:12])
        if len(ids) > 12:
            id_preview += f", … (+{len(ids) - 12} more)"
        changes.append(
            entry(
                "Database",
                "Cleared all Orchestrator records",
                "Deleted every row from images, tasks, task_images, and annotations. "
                "Activity feed and completed-job poller state were also reset.",
            )
        )
        changes.append(
            entry(
                "CVAT",
                f"Deleted {deleted} task(s) from sandbox project",
                "REST API DELETE /api/tasks/{id} for every task in the configured CVAT project. "
                + (f"Removed: {id_preview}. " if id_preview else "No tasks found. ")
                + (f"{err_n} delete error(s)." if err_n else "CVAT project tasks wiped."),
            )
        )
    elif action == "seed":
        changes.append(
            entry(
                "Image",
                f"Registered {data.get('image_id')} → images table (row #{data.get('orchestrator_image_id')})",
                "Register image (seed): Orchestrator stores locally that this image exists, "
                "before any CVAT handoff. "
                f"File: {data.get('storage_path')}. "
                "workflow_state=validated — simulates one image ingested from Kobo, processed and stored "
                "(ingest.py → Processed/).",
            )
        )
    elif action == "push":
        changes.append(
            entry(
                "Task",
                f"Created {data.get('task_ref')} → CVAT task #{data.get('external_task_id')}",
                "REST API POST /api/tasks: Orchestrator asked CVAT to create an annotation job. "
                f"Mapped task_ref {data.get('task_ref')} to CVAT external_task_id {data.get('external_task_id')}. "
                f"Handoff URL: {data.get('handoff_url')}",
            )
        )
        changes.append(
            entry(
                "TaskImage",
                f"Linked {data.get('image_id')} ↔ {data.get('task_ref')} (task_images)",
                "REST API POST /api/tasks/{id}/data: uploaded the file to CVAT as "
                f"{data.get('upload_filename')}. "
                "Created a task_images junction row so Orchestrator knows "
                "which image belongs to which annotation job.",
            )
        )
        changes.append(
            entry(
                "Image",
                f"{data.get('image_id')} → workflow_state queued_for_annotation",
                "Updated the images row: ready for human annotation in CVAT. "
                "Metadata (farm, volunteer, GPS, notes) stays in Orchestrator only — "
                "CVAT received filename + task name + labels only.",
            )
        )
    elif action == "pull":
        for item in data.get("images", []):
            if item.get("skipped"):
                changes.append(
                    entry(
                        "Annotation",
                        f"Skipped {item['citizen_ai_image_id']} (already imported)",
                        "An active annotation already exists for this task+image. "
                        "Enable Force re-import to replace it with a fresh COCO export from CVAT.",
                    )
                )
            else:
                changes.append(
                    entry(
                        "Annotation",
                        f"Imported {item.get('labels_count', 0)} label(s) for {item['citizen_ai_image_id']}",
                        "REST API export COCO 1.0 from CVAT → normalized to Orchestrator payload JSON → "
                        "stored in annotations table. Raw export saved under exports/raw/.",
                    )
                )
                changes.append(
                    entry(
                        "Image",
                        f"{item['citizen_ai_image_id']} → workflow_state annotated",
                        "Marked the image as annotated in Orchestrator. "
                        "The bounding box/polygon now lives in Annotation.payload, linked back via citizen_ai_image_id.",
                    )
                )
        if data.get("created", 0) or data.get("images"):
            changes.append(
                entry(
                    "Task",
                    f"{data.get('task_ref')} → status annotated",
                    "Task marked annotated after COCO import. TaskImage link is unchanged.",
                )
            )
    elif action == "status":
        changes.append(
            entry(
                "Snapshot",
                "Refreshed entity panel and CVAT live status",
                "Read-only check: compares local SQLite state with CVAT task size/jobs via GET /api/tasks/{id}.",
            )
        )
    elif action == "register_webhook":
        changes.append(
            entry(
                "Webhook",
                f"CVAT webhook #{data.get('webhook_id')} → Orchestrator",
                "Registered project webhook for event update:job. "
                "Production rule: Orchestrator auto-pulls only when job state becomes completed "
                f"(not on every Save). Target: {data.get('target_url')}.",
            )
        )
    elif action == "webhook":
        if data.get("action") == "auto_pull":
            labels = (data.get("pull") or {}).get("total_labels", 0)
            changes.append(
                entry(
                    "Webhook",
                    f"update:job completed → auto-pull {data.get('task_ref')} (labels={labels})",
                    (
                        "Poller detected CVAT job state=completed (Docker Desktop blocks CVAT "
                        "webhook egress to private IPs — same pull path as a real webhook). "
                        if data.get("source") == "poller"
                        else "CVAT POSTed update:job with state=completed. "
                    )
                    + "Orchestrator mapped CVAT task → task_ref and ran COCO export + normalize. "
                    "Save alone does not trigger pull.",
                )
            )
            for item in (data.get("pull") or {}).get("images", []):
                if item.get("skipped"):
                    changes.append(
                        entry(
                            "Annotation",
                            f"Skipped {item['citizen_ai_image_id']} (already imported)",
                            "Active annotation already present; webhook pull used force=true so this is unexpected.",
                        )
                    )
                else:
                    changes.append(
                        entry(
                            "Annotation",
                            f"Imported {item.get('labels_count', 0)} label(s) for {item['citizen_ai_image_id']}",
                            "Auto-imported via webhook after job completed.",
                        )
                    )
                    changes.append(
                        entry(
                            "Image",
                            f"{item['citizen_ai_image_id']} → workflow_state annotated",
                            "Marked annotated after webhook-triggered pull.",
                        )
                    )
            pull = data.get("pull") or {}
            if pull.get("created", 0) or pull.get("images"):
                changes.append(
                    entry(
                        "Task",
                        f"{data.get('task_ref')} → status annotated",
                        "Task marked annotated after auto-pull. TaskImage link is unchanged.",
                    )
                )
        else:
            changes.append(
                entry(
                    "Webhook",
                    f"Received {data.get('event')} — {data.get('action')}",
                    data.get("reason")
                    or "Webhook received but did not trigger auto-pull.",
                )
            )
    return changes


def demo_state(settings: PocSettings | None = None) -> dict[str, Any]:
    settings = settings or PocSettings()
    task_ref = active_demo_task_ref(settings)
    next_ref = next_demo_task_ref(settings)
    status = get_status(task_ref=task_ref, settings=settings)
    # If active ref has no row yet, status is empty — that's fine pre-push.
    task_entry = status["tasks"][0] if status["tasks"] else None
    image = _db(settings).get_image_by_citizen_id(DEMO_IMAGE_ID)
    webhook_info: dict[str, Any] | None = None
    try:
        listed = list_cvat_webhooks(settings=settings)
        preferred = resolve_webhook_target(DEFAULT_WEBHOOK_TARGET)
        match = [
            w
            for w in listed.get("webhooks", [])
            if w.get("is_active")
            and (
                w.get("target_url") == preferred
                or w.get("target_url") == DEFAULT_WEBHOOK_TARGET
                or ":5050/api/webhooks/cvat" in (w.get("target_url") or "")
            )
        ]
        if match:
            webhook_info = {
                "id": match[0].get("id"),
                "target_url": match[0].get("target_url"),
                "events": match[0].get("events"),
                "is_active": True,
            }
    except Exception:  # noqa: BLE001 - demo helper; CVAT may be down
        webhook_info = None
    return {
        "demo": {
            "image_id": DEMO_IMAGE_ID,
            "image_path": str(DEMO_IMAGE_PATH),
            "task_ref": task_entry["task"]["task_ref"] if task_entry else next_ref,
            "next_task_ref": next_ref,
            "metadata": DEMO_METADATA,
            "webhook_target": DEFAULT_WEBHOOK_TARGET,
        },
        "db_exists": settings.db_path.is_file(),
        "image_registered": image is not None,
        "task": task_entry,
        "webhook": webhook_info,
        "webhook_feed": get_webhook_feed(),
    }
