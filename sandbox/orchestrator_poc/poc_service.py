"""Shared Orchestrator CVAT POC service layer (CLI + demo UI)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cvat_adapter import CvatClient, CvatConfig, EXPORT_FORMAT
from db import Database
from normalize_coco import extract_json_bytes_from_zip, normalize_coco_for_image, save_normalized_payload

POC_DIR = Path(__file__).resolve().parent
DEFAULT_DB = POC_DIR / "poc.db"
DEFAULT_CONFIG = POC_DIR.parent / "cvat-sandbox-info.json"
RAW_EXPORT_DIR = POC_DIR / "exports" / "raw"
NORMALIZED_EXPORT_DIR = POC_DIR / "exports" / "normalized"

DEMO_IMAGE_ID = "CAI-WP01-IMG-000001"
DEMO_IMAGE_PATH = POC_DIR.parent / "cvat-dummy-images" / "sandbox_farmA_vol001_20250713_topdown_01.jpg"
DEMO_TASK_REF = "TASK-0002"
DEMO_METADATA = {"farm": "farmA", "volunteer_id": "vol001", "photo_type": "topdown"}


@dataclass
class PocSettings:
    db_path: Path = DEFAULT_DB
    config_path: Path = DEFAULT_CONFIG


class PocError(Exception):
    pass


def load_config(config_path: Path) -> CvatConfig:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return CvatConfig.from_dict(data)


def _db(settings: PocSettings) -> Database:
    return Database(settings.db_path)


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
        "workflow_state": "registered",
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
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    db.clear_all_records()
    return {"cleared": True}


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
        changes.append(
            entry(
                "Database",
                "Cleared all Orchestrator records",
                "Deleted every row from images, tasks, task_images, and annotations. "
                "CVAT tasks on the server are untouched — only local Orchestrator state was wiped.",
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
                "In production this is a validated image after ingestion (ingest.py → Processed/).",
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
    elif action == "status":
        changes.append(
            entry(
                "Snapshot",
                "Refreshed entity panel and CVAT live status",
                "Read-only check: compares local SQLite state with CVAT task size/jobs via GET /api/tasks/{id}.",
            )
        )
    return changes


def demo_state(settings: PocSettings | None = None) -> dict[str, Any]:
    settings = settings or PocSettings()
    status = get_status(task_ref=DEMO_TASK_REF, settings=settings)
    task_entry = status["tasks"][0] if status["tasks"] else None
    image = _db(settings).get_image_by_citizen_id(DEMO_IMAGE_ID)
    return {
        "demo": {
            "image_id": DEMO_IMAGE_ID,
            "image_path": str(DEMO_IMAGE_PATH),
            "task_ref": DEMO_TASK_REF,
            "metadata": DEMO_METADATA,
        },
        "db_exists": settings.db_path.is_file(),
        "image_registered": image is not None,
        "task": task_entry,
    }
