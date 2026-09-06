"""Shared Orchestrator CVAT POC service layer (CLI + demo UI)."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cvat_adapter import CvatClient, CvatConfig, EXPORT_FORMAT
from db import Database
from kobo_adapter import (
    KoboClient,
    KoboConfig,
    extract_photo_candidates,
    safe_stem,
)
from label_studio_adapter import (
    EXPORT_FORMAT as LS_EXPORT_FORMAT,
    PLATFORM as LS_PLATFORM,
    LabelStudioClient,
    LabelStudioConfig,
)
from normalize_coco import extract_json_bytes_from_zip, normalize_coco_for_image, save_normalized_payload
from normalize_ls import normalize_ls_annotations_for_image, triage_decision

POC_DIR = Path(__file__).resolve().parent
DEFAULT_DB = POC_DIR / "poc.db"
DEFAULT_CONFIG = Path(
    os.environ.get("CVAT_CONFIG") or (POC_DIR.parent / "cvat-sandbox-info.json")
)
DEFAULT_LS_CONFIG = Path(
    os.environ.get("LABEL_STUDIO_CONFIG")
    or (POC_DIR.parent / "label-studio-sandbox-info.json")
)
DEFAULT_KOBO_CONFIG = Path(
    os.environ.get("KOBO_CONFIG") or (POC_DIR.parent / "kobo-sandbox-info.json")
)
KOBO_MEDIA_DIR = POC_DIR / "kobo_media"
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
DEFAULT_LS_WEBHOOK_TARGET = os.environ.get(
    "LS_WEBHOOK_TARGET_URL",
    "http://orchestrator_poc:5050/api/webhooks/label_studio",
)

# In-memory feed for the demo UI (newest last). Not persisted.
_WEBHOOK_FEED: list[dict[str, Any]] = []
_WEBHOOK_FEED_MAX = 50
# job_ids already auto-pulled (or claimed) while state=completed
_COMPLETED_PULLS: set[str] = set()
_LS_COMPLETED_PULLS: set[str] = set()
_PULL_LOCK = threading.Lock()


@dataclass
class PocSettings:
    db_path: Path = DEFAULT_DB
    config_path: Path = DEFAULT_CONFIG
    ls_config_path: Path = DEFAULT_LS_CONFIG
    kobo_config_path: Path = DEFAULT_KOBO_CONFIG
    platform: str = os.environ.get("DEMO_PLATFORM", "cvat")


class PocError(Exception):
    pass


def normalize_platform(platform: str | None) -> str:
    p = (platform or "cvat").strip().lower().replace("-", "_")
    if p in ("ls", "labelstudio", "label_studio"):
        return "label_studio"
    return "cvat"


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


def _persist_cvat_project_id(config_path: Path, project_id: int) -> None:
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("project_id") or 0) == int(project_id):
        return
    data["project_id"] = int(project_id)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_ls_config(config_path: Path | None = None) -> LabelStudioConfig:
    path = config_path or DEFAULT_LS_CONFIG
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    api_base = os.environ.get("LABEL_STUDIO_API_BASE_URL")
    public_base = os.environ.get("LABEL_STUDIO_PUBLIC_BASE_URL")
    if api_base:
        data = {**data, "base_url": api_base.rstrip("/")}
    if public_base:
        data = {**data, "public_base_url": public_base.rstrip("/")}
    return LabelStudioConfig.from_dict(data)


def load_kobo_config(config_path: Path | None = None) -> KoboConfig:
    path = config_path or DEFAULT_KOBO_CONFIG
    if not Path(path).is_file():
        raise PocError(
            f"Kobo config not found: {path}. "
            "Copy sandbox/kobo-sandbox-info.json.example → kobo-sandbox-info.json"
        )
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return KoboConfig.from_dict(data)
    except ValueError as exc:
        raise PocError(str(exc)) from exc


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
    try:
        ls = LabelStudioClient(load_ls_config(settings.ls_config_path))
        if ls.config.api_token:
            for task in ls.list_project_tasks():
                meta = task.get("meta") or {}
                match = _TASK_REF_RE.match(str(meta.get("task_ref") or ""))
                if match:
                    nums.append(int(match.group(1)))
    except Exception:  # noqa: BLE001
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
    workflow_state: str = "ingested",
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    image_path = Path(path).resolve()
    if not image_path.is_file():
        raise PocError(f"Image not found: {image_path}")

    meta = metadata or {}
    row_id = db.seed_image(image_id, str(image_path), meta, workflow_state=workflow_state)
    return {
        "image_id": image_id,
        "orchestrator_image_id": row_id,
        "storage_path": str(image_path),
        "metadata": meta,
        "workflow_state": workflow_state,
    }


def _already_pulled_kobo_submission_ids(settings: PocSettings) -> set[str]:
    db = _db(settings)
    db.init_db()
    ids: set[str] = set()
    with db.connect() as conn:
        for row in conn.execute("SELECT metadata_json FROM images").fetchall():
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                continue
            sid = meta.get("kobo_submission_id")
            if sid:
                ids.add(str(sid))
    return ids


def pull_from_kobo(
    *,
    limit: int = 1,
    auto_push_ls: bool = False,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    """
    Pull the next Kobo submission in reverse chronological order (newest first),
    only on/after config start_date (default 2026-06-25). One click → one submission.
    Saves Image rows only. LS push is a separate manual action unless auto_push_ls=True.
    """
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    KOBO_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    config = load_kobo_config(settings.kobo_config_path)
    client = KoboClient(config)
    already = _already_pulled_kobo_submission_ids(settings)
    # limit kept for API compat; demo always uses 1
    to_pull = max(1, min(int(limit or 1), 5))

    imported: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []

    try:
        for _ in range(to_pull):
            sub = client.next_submission_after(
                already_pulled_ids=already,
                start_date=config.start_date,
            )
            if sub is None:
                break
            submissions.append(sub)
            sid = str(sub.get("_id") or "")
            if sid:
                already.add(sid)
    except Exception as exc:  # noqa: BLE001
        raise PocError(f"Kobo list submissions failed: {exc}") from exc

    if not submissions:
        return {
            "submissions_fetched": 0,
            "images_imported": 0,
            "imported": [],
            "errors": [],
            "asset_uid": config.asset_uid,
            "base_url": config.base_url,
            "start_date": config.start_date,
            "already_pulled_count": len(_already_pulled_kobo_submission_ids(settings)),
            "note": (
                f"No more submissions on/after {config.start_date} "
                "(newest-first cursor exhausted). Reset All to restart."
            ),
        }

    for sub in submissions:
        submission_id = str(sub.get("_id") or sub.get("id") or "")
        if not submission_id:
            errors.append({"reason": "submission missing _id", "raw_keys": list(sub.keys())[:12]})
            continue
        photos = extract_photo_candidates(sub)
        if not photos:
            errors.append({"submission_id": submission_id, "reason": "no photos found"})
            continue

        farm = str(sub.get("Farm_location") or sub.get("farm") or "unknown")
        volunteer = str(sub.get("Volunteer_ID") or sub.get("volunteer_id") or "unknown")
        submitted = str(sub.get("_submission_time") or "")

        for photo in photos:
            slot = photo["slot"]
            citizen_id = f"CAI-KOBO-{safe_stem(submission_id)}-{slot}"
            dest = KOBO_MEDIA_DIR / f"{citizen_id}.jpg"
            try:
                url = client.resolve_media_url(sub, photo["filename"], photo["url"])
                client.download_file(url, dest)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "submission_id": submission_id,
                        "slot": slot,
                        "reason": f"download failed: {exc}",
                    }
                )
                continue

            meta = {
                "source": "kobo",
                "kobo_submission_id": submission_id,
                "farm": farm,
                "volunteer_id": volunteer,
                "photo_type": slot,
                "original_filename": photo["filename"],
                "submission_time": submitted,
            }
            seeded = seed_image(
                image_id=citizen_id,
                path=dest,
                metadata=meta,
                settings=settings,
                workflow_state="ingested",
            )
            entry: dict[str, Any] = {
                **seeded,
                "kobo_submission_id": submission_id,
                "submission_time": submitted,
            }
            if auto_push_ls:
                ls_ref = f"LS-{safe_stem(submission_id)}-{slot}"
                existing = db.get_task_by_ref(ls_ref)
                if existing is None:
                    try:
                        pushed = push_to_label_studio(
                            image_id=citizen_id,
                            task_ref=ls_ref,
                            settings=settings,
                        )
                        entry["ls_push"] = pushed
                    except Exception as exc:  # noqa: BLE001
                        entry["ls_push_error"] = str(exc)
                        errors.append(
                            {
                                "submission_id": submission_id,
                                "image_id": citizen_id,
                                "reason": f"LS push failed: {exc}",
                            }
                        )
                else:
                    entry["ls_push"] = {
                        "skipped": True,
                        "task_ref": ls_ref,
                        "external_task_id": existing["external_task_id"],
                        "handoff_url": existing["handoff_url"],
                    }
            imported.append(entry)

    return {
        "submissions_fetched": len(submissions),
        "images_imported": len(imported),
        "imported": imported,
        "errors": errors,
        "asset_uid": config.asset_uid,
        "base_url": config.base_url,
        "start_date": config.start_date,
        "order": "newest_first",
        "pulled_submission_ids": [
            str(s.get("_id")) for s in submissions if s.get("_id") is not None
        ],
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
    _persist_cvat_project_id(settings.config_path, int(result.external_project_id))

    task_id = db.create_task(
        task_ref=task_ref,
        external_project_id=result.external_project_id,
        external_task_id=result.external_task_id,
        handoff_url=result.handoff_url,
        external_platform="cvat",
    )
    db.link_task_image(task_id, image["id"])
    db.update_image_workflow_state(image["id"], "in_cvat")

    return {
        "image_id": image_id,
        "task_ref": task_ref,
        "orchestrator_task_id": task_id,
        "external_task_id": result.external_task_id,
        "handoff_url": result.handoff_url,
        "upload_filename": upload_name,
        "external_platform": "cvat",
        "stage": "cvat_annotation",
    }


def push_to_label_studio(
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
            f"Task ref already exists: {task_ref} -> task {existing['external_task_id']}"
        )

    image_path = Path(image["storage_path"])
    if not image_path.is_file():
        raise PocError(f"Image file missing: {image_path}")

    config = load_ls_config(settings.ls_config_path)
    client = LabelStudioClient(config)
    client.signup_or_login()
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
        external_platform=LS_PLATFORM,
    )
    db.link_task_image(task_id, image["id"])
    db.update_image_workflow_state(image["id"], "in_ls_triage")

    return {
        "image_id": image_id,
        "task_ref": task_ref,
        "orchestrator_task_id": task_id,
        "external_task_id": result.external_task_id,
        "handoff_url": result.handoff_url,
        "upload_filename": upload_name,
        "external_platform": LS_PLATFORM,
        "stage": "ls_triage",
    }


def push_to_platform(
    *,
    image_id: str,
    task_ref: str,
    platform: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    plat = normalize_platform(platform or settings.platform)
    if plat == "label_studio":
        return push_to_label_studio(image_id=image_id, task_ref=task_ref, settings=settings)
    return push_to_cvat(image_id=image_id, task_ref=task_ref, settings=settings)


def latest_citizen_image_id(settings: PocSettings | None = None) -> str | None:
    """Most recently created image in SQLite (for manual CVAT push in the demo)."""
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT citizen_ai_image_id FROM images ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row["citizen_ai_image_id"] if row else None


def push_latest_to_cvat(
    *,
    image_id: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    """
    Manual demo action: push an image to CVAT (no LS triage gate).
    Defaults to the most recently ingested/registered image.
    """
    settings = settings or PocSettings()
    cid = image_id or latest_citizen_image_id(settings)
    if not cid:
        raise PocError("No images in DB. Pull from Kobo or Register dummy first.")
    task_ref = f"CVAT-{safe_stem(cid)}"
    db = _db(settings)
    existing = db.get_task_by_ref(task_ref)
    if existing is not None:
        return {
            "skipped": True,
            "reason": "CVAT task already exists for this image",
            "image_id": cid,
            "task_ref": task_ref,
            "external_task_id": existing["external_task_id"],
            "handoff_url": existing["handoff_url"],
            "external_platform": "cvat",
        }
    return push_to_cvat(image_id=cid, task_ref=task_ref, settings=settings)


def push_latest_to_ls(
    *,
    image_id: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    """Manual demo action: push the latest (or given) image to Label Studio for triage."""
    settings = settings or PocSettings()
    cid = image_id or latest_citizen_image_id(settings)
    if not cid:
        raise PocError("No images in DB. Pull from Kobo or Register dummy first.")
    task_ref = f"LS-{safe_stem(cid)}"
    db = _db(settings)
    existing = db.get_task_by_ref(task_ref)
    if existing is not None:
        return {
            "skipped": True,
            "reason": "LS task already exists for this image",
            "image_id": cid,
            "task_ref": task_ref,
            "external_task_id": existing["external_task_id"],
            "handoff_url": existing["handoff_url"],
            "external_platform": LS_PLATFORM,
        }
    return push_to_label_studio(image_id=cid, task_ref=task_ref, settings=settings)


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


def pull_from_label_studio(
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

    config = load_ls_config(settings.ls_config_path)
    client = LabelStudioClient(config)
    client.signup_or_login()
    ls_task = client.get_task(task["external_task_id"])
    annotations = list(ls_task.get("annotations") or [])

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = RAW_EXPORT_DIR / f"{task_ref}_{timestamp}_ls.json"
    raw_path.write_text(json.dumps(ls_task, indent=2), encoding="utf-8")

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

        payload = normalize_ls_annotations_for_image(
            annotations,
            image_ref=image["citizen_ai_image_id"],
            external_task_id=task["external_task_id"],
            export_format=LS_EXPORT_FORMAT,
        )
        normalized_path = NORMALIZED_EXPORT_DIR / f"{task_ref}_{image['citizen_ai_image_id']}.json"
        save_normalized_payload(payload, normalized_path)

        annotation_id = db.create_annotation(
            task_id=task["id"],
            image_id=image["id"],
            payload=payload,
            export_format=LS_EXPORT_FORMAT,
            raw_export_path=str(raw_path),
        )
        decision = triage_decision(payload)
        # Record triage outcome; CVAT push is manual (demo UI / API) — no auto-gate.
        if decision == "valid":
            db.update_image_workflow_state(image["id"], "triaged_valid")
        elif decision == "rejected":
            db.update_image_workflow_state(image["id"], "rejected_at_triage")
        elif decision == "unsure":
            db.update_image_workflow_state(image["id"], "triage_unsure")
        else:
            db.update_image_workflow_state(image["id"], "triaged")

        created += 1
        results.append(
            {
                "citizen_ai_image_id": image["citizen_ai_image_id"],
                "annotation_id": annotation_id,
                "labels_count": len(payload["labels"]),
                "payload": payload,
                "normalized_path": str(normalized_path),
                "triage_decision": decision,
            }
        )

    db.update_task_status(task["id"], "triaged")
    return {
        "task_ref": task_ref,
        "created": created,
        "skipped": skipped,
        "raw_export_path": str(raw_path),
        "images": results,
        "total_labels": sum(item.get("labels_count", 0) for item in results),
        "external_platform": LS_PLATFORM,
        "stage": "ls_triage_complete",
        "note": "CVAT push is manual — use Push to CVAT in the demo UI",
    }


def pull_from_platform(
    *,
    task_ref: str,
    force: bool = False,
    platform: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    task = db.get_task_by_ref(task_ref)
    plat = normalize_platform(
        platform
        or (task["external_platform"] if task else None)
        or settings.platform
    )
    if plat == "label_studio":
        return pull_from_label_studio(task_ref=task_ref, force=force, settings=settings)
    return pull_from_cvat(task_ref=task_ref, force=force, settings=settings)


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

    tasks: list[dict[str, Any]] = []
    for item in report:
        task = dict(item["task"])
        entry: dict[str, Any] = {
            "task": task,
            "images": item["images"],
            "annotations": [],
            "cvat_live": None,
            "ls_live": None,
        }
        for ann in item["annotations"]:
            payload = json.loads(ann["payload_json"])
            entry["annotations"].append(
                {
                    **dict(ann),
                    "labels_count": len(payload.get("labels", [])),
                    "payload": payload,
                }
            )
        plat = normalize_platform(task.get("external_platform"))
        if task.get("external_task_id") and plat == "cvat":
            try:
                client = CvatClient(load_config(settings.config_path))
                cvat_task = client.get_task(task["external_task_id"])
                entry["cvat_live"] = {
                    "size": cvat_task.get("size"),
                    "jobs": cvat_task.get("jobs", {}).get("count"),
                }
            except Exception as exc:  # noqa: BLE001 - demo status helper
                entry["cvat_live"] = {"error": str(exc)}
        elif task.get("external_task_id") and plat == "label_studio":
            try:
                client = LabelStudioClient(load_ls_config(settings.ls_config_path))
                ls_task = client.get_task(task["external_task_id"])
                entry["ls_live"] = {
                    "is_labeled": ls_task.get("is_labeled"),
                    "annotations_count": len(ls_task.get("annotations") or []),
                }
            except Exception as exc:  # noqa: BLE001
                entry["ls_live"] = {"error": str(exc)}
        tasks.append(entry)

    return {"tasks": tasks}


def get_entities(settings: PocSettings | None = None) -> dict[str, list[dict[str, Any]]]:
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    return db.list_all_entities()


def reset_all(
    settings: PocSettings | None = None,
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    """Clear Orchestrator SQLite and delete tasks on the active annotation platform."""
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    plat = normalize_platform(platform or settings.platform)

    cvat_result: dict[str, Any] = {
        "deleted_count": 0,
        "deleted_task_ids": [],
        "errors": [],
        "skipped": plat != "cvat",
    }
    ls_result: dict[str, Any] = {
        "deleted_count": 0,
        "deleted_task_ids": [],
        "errors": [],
        "skipped": plat != "label_studio",
    }

    if plat == "cvat":
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
    else:
        try:
            client = LabelStudioClient(load_ls_config(settings.ls_config_path))
            client.signup_or_login()
            ls_result = client.delete_all_project_tasks()
        except Exception as exc:  # noqa: BLE001
            ls_result = {
                "deleted_count": 0,
                "deleted_task_ids": [],
                "errors": [{"error": str(exc)}],
                "skipped": False,
            }

    db.clear_all_records()
    if KOBO_MEDIA_DIR.is_dir():
        shutil.rmtree(KOBO_MEDIA_DIR, ignore_errors=True)
    KOBO_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    _WEBHOOK_FEED.clear()
    _COMPLETED_PULLS.clear()
    _LS_COMPLETED_PULLS.clear()

    return {
        "cleared": True,
        "platform": plat,
        "cvat": cvat_result,
        "label_studio": ls_result,
        "kobo_cursor": "reset — next Pull starts at newest on/after start_date",
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


def register_ls_webhook(
    *,
    target_url: str | None = None,
    settings: PocSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    client = LabelStudioClient(load_ls_config(settings.ls_config_path))
    client.signup_or_login()
    target = target_url or DEFAULT_LS_WEBHOOK_TARGET
    webhook = client.ensure_project_webhook(target_url=target)
    return {
        "webhook_id": webhook.get("id"),
        "target_url": webhook.get("url") or target,
        "requested_target_url": target,
        "actions": webhook.get("actions"),
        "project_id": webhook.get("project") or client.config.project_id,
        "is_active": True,
    }


def list_ls_webhooks(settings: PocSettings | None = None) -> dict[str, Any]:
    settings = settings or PocSettings()
    client = LabelStudioClient(load_ls_config(settings.ls_config_path))
    try:
        client.signup_or_login()
        webhooks = client.list_project_webhooks()
    except Exception:  # noqa: BLE001
        return {"webhooks": [], "count": 0}
    return {"webhooks": webhooks, "count": len(webhooks)}


def handle_ls_webhook(
    payload: dict[str, Any],
    *,
    settings: PocSettings | None = None,
    force_pull: bool = True,
) -> dict[str, Any]:
    """Auto-pull when Label Studio reports TASK_COMPLETED or ANNOTATION_CREATED."""
    settings = settings or PocSettings()
    action = (
        payload.get("action")
        or payload.get("event")
        or (payload.get("webhook") or {}).get("action")
        or "unknown"
    )
    task_payload = payload.get("task") or {}
    if isinstance(task_payload, int):
        ls_task_id = str(task_payload)
        task_payload = {"id": task_payload}
    else:
        ls_task_id = str(task_payload.get("id") or payload.get("task_id") or "")

    feed_id = len(_WEBHOOK_FEED) + 1
    base_feed = {
        "id": feed_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "event": str(action),
        "ls_task_id": ls_task_id or None,
        "source": payload.get("source", "webhook"),
        "platform": LS_PLATFORM,
    }

    actionable = str(action).upper() in {
        "TASK_COMPLETED",
        "ANNOTATION_CREATED",
        "ANNOTATIONS_CREATED",
        "ANNOTATION_UPDATED",
    }
    if not actionable or not ls_task_id:
        result = {
            **base_feed,
            "action": "ignored",
            "reason": f"Not a completed/annotated LS event ({action})",
        }
        # Do not spam the demo feed with PROJECT_UPDATED / TASKS_CREATED noise.
        if str(action).upper() in {
            "ANNOTATION_CREATED",
            "ANNOTATIONS_CREATED",
            "ANNOTATION_UPDATED",
            "TASK_COMPLETED",
        }:
            _append_webhook_feed(result)
        return result

    claim_key = f"ls:{ls_task_id}"
    with _PULL_LOCK:
        if claim_key in _LS_COMPLETED_PULLS and str(action).upper() != "ANNOTATION_UPDATED":
            return {
                **base_feed,
                "action": "ignored",
                "reason": f"LS task #{ls_task_id} already auto-pulled",
            }
        _LS_COMPLETED_PULLS.add(claim_key)

    db = _db(settings)
    db.init_db()
    task = db.get_task_by_external_id(ls_task_id)
    if task is None:
        # Try meta.task_ref from payload
        meta = task_payload.get("meta") or {}
        ref = meta.get("task_ref")
        if ref:
            task = db.get_task_by_ref(str(ref))
    if task is None:
        with _PULL_LOCK:
            _LS_COMPLETED_PULLS.discard(claim_key)
        result = {
            **base_feed,
            "action": "ignored",
            "reason": f"No Orchestrator task mapped to LS task #{ls_task_id}",
        }
        _append_webhook_feed(result)
        return result

    try:
        pull = pull_from_label_studio(
            task_ref=task["task_ref"],
            force=force_pull,
            settings=settings,
        )
    except Exception:
        with _PULL_LOCK:
            _LS_COMPLETED_PULLS.discard(claim_key)
        raise

    image_summaries: list[dict[str, Any]] = []
    for img in pull.get("images", []):
        payload = img.get("payload") or {}
        labels = payload.get("labels") or []
        image_summaries.append(
            {
                "citizen_ai_image_id": img.get("citizen_ai_image_id"),
                "labels_count": img.get("labels_count", 0),
                "skipped": img.get("skipped", False),
                "triage_decision": img.get("triage_decision"),
                "triage": payload.get("triage"),
                "label_names": [str(lab.get("name") or "") for lab in labels],
                "geometries": [str(lab.get("geometry_type") or "") for lab in labels],
                "raw_export_path": pull.get("raw_export_path"),
            }
        )

    result = {
        **base_feed,
        "action": "auto_pull",
        "task_ref": task["task_ref"],
        "external_task_id": ls_task_id or task.get("external_task_id"),
        "pull": {
            "created": pull.get("created"),
            "skipped": pull.get("skipped"),
            "total_labels": pull.get("total_labels"),
            "raw_export_path": pull.get("raw_export_path"),
            "images": image_summaries,
        },
    }
    _append_webhook_feed(result)
    return result


def poll_ls_completed_tasks(
    *,
    settings: PocSettings | None = None,
    force_pull: bool = False,
) -> list[dict[str, Any]]:
    """Fallback poller: LS tasks with annotations / is_labeled → auto-pull."""
    settings = settings or PocSettings()
    db = _db(settings)
    db.init_db()
    results: list[dict[str, Any]] = []
    try:
        client = LabelStudioClient(load_ls_config(settings.ls_config_path))
        if not client.config.api_token:
            return results
    except Exception as exc:  # noqa: BLE001
        return [{"action": "error", "reason": str(exc)}]

    with db.connect() as conn:
        tasks = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM tasks WHERE external_platform = ?",
                (LS_PLATFORM,),
            ).fetchall()
        ]

    for task in tasks:
        ext = task.get("external_task_id")
        if not ext:
            continue
        claim_key = f"ls:{ext}"
        images = db.get_task_images(task["id"])
        if images and all(
            db.get_active_annotation(task["id"], img["id"]) is not None for img in images
        ):
            with _PULL_LOCK:
                _LS_COMPLETED_PULLS.add(claim_key)
            continue
        try:
            ls_task = client.get_task(ext)
        except Exception as exc:  # noqa: BLE001
            results.append({"action": "error", "reason": str(exc), "ls_task_id": ext})
            continue
        anns = ls_task.get("annotations") or []
        if not anns and not ls_task.get("is_labeled"):
            with _PULL_LOCK:
                _LS_COMPLETED_PULLS.discard(claim_key)
            continue
        with _PULL_LOCK:
            if claim_key in _LS_COMPLETED_PULLS:
                continue
        try:
            pull_result = handle_ls_webhook(
                {
                    "action": "TASK_COMPLETED",
                    "task": ls_task,
                    "source": "poller",
                },
                settings=settings,
                force_pull=force_pull,
            )
            if pull_result.get("action") == "auto_pull":
                results.append(pull_result)
        except Exception as exc:  # noqa: BLE001
            results.append({"action": "error", "reason": str(exc), "ls_task_id": ext})
    return results


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
        "platform": "cvat",
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
        if normalize_platform(task.get("external_platform")) != "cvat":
            continue
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
    """Short demo activity-log lines: entity change + transport (no paths/URLs/filenames)."""
    changes: list[dict[str, str]] = []

    def entry(
        entity: str,
        summary: str,
        detail: str,
        *,
        how: str | None = None,
        how_detail: str | None = None,
    ) -> dict[str, str]:
        out: dict[str, str] = {"entity": entity, "summary": summary, "detail": detail}
        if how:
            out["how"] = how
        if how_detail:
            out["how_detail"] = how_detail
        return out

    def short_id(value: Any) -> str:
        text = str(value or "—")
        # Prefer the meaningful tail (…-topdown) over the long CAI-KOBO-… prefix in UI.
        if "CAI-KOBO-" in text:
            parts = text.split("-")
            if len(parts) >= 2:
                return "-".join(parts[-2:])  # e.g. 790199718-topdown
        return text

    if action == "init":
        changes.append(
            entry(
                "Database",
                "Schema ready",
                "Local tables created for this POC.",
            )
        )
    elif action == "reset":
        deleted = (data.get("cvat") or {}).get("deleted_count", 0)
        changes.append(
            entry(
                "Database",
                "Orchestrator records cleared",
                "images / tasks / task_images / annotations emptied.",
            )
        )
        changes.append(
            entry(
                "CVAT",
                f"Deleted {deleted} remote task(s)",
                "Sandbox project tasks removed via REST API.",
                how="Via CVAT REST API",
                how_detail="Bulk delete of project tasks.",
            )
        )
    elif action == "seed":
        changes.append(
            entry(
                "Image",
                f"Registered {short_id(data.get('image_id'))} → ingested",
                "Local Image row created (before LS/CVAT handoff).",
            )
        )
    elif action == "pull_kobo":
        imported = data.get("imported") or []
        n = data.get("images_imported") or len(imported)
        if n:
            ids = ", ".join(
                short_id(i.get("image_id") or i.get("citizen_ai_image_id")) for i in imported[:4]
            )
            if len(imported) > 4:
                ids += f" (+{len(imported) - 4})"
            changes.append(
                entry(
                    "Image",
                    f"Pulled {n} from Kobo → ingested",
                    f"Registered: {ids}. Push to LS/CVAT is separate.",
                    how="Via Kobo REST API",
                    how_detail="Fetched submission media; wrote local Image rows.",
                )
            )
        else:
            changes.append(
                entry(
                    "Image",
                    "No new Kobo images",
                    data.get("note") or "Nothing new for this pull.",
                )
            )
    elif action in ("push", "push_ls", "push_cvat"):
        plat = data.get("external_platform") or (
            "label_studio" if action == "push_ls" else "cvat"
        )
        is_ls = plat == "label_studio"
        plat_label = "LS" if is_ls else "CVAT"
        if data.get("skipped"):
            changes.append(
                entry(
                    "Task",
                    f"Skipped — already on {plat_label}",
                    data.get("reason") or "Remote task already linked.",
                )
            )
        else:
            wf = "in_ls_triage" if is_ls else "in_cvat"
            ext_task = data.get("external_task_id")
            img = short_id(data.get("image_id"))
            changes.append(
                entry(
                    "Task",
                    f"Created → {plat_label} #{ext_task}",
                    f"{'LS triage' if is_ls else 'CVAT annotation'} job opened.",
                    how=f"Via {plat_label} REST API",
                    how_detail="Created remote task and attached image bytes.",
                )
            )
            changes.append(
                entry(
                    "TaskImage",
                    f"Linked {img} ↔ {plat_label} #{ext_task}",
                    "Local Image connected to remote task.",
                )
            )
            changes.append(
                entry(
                    "Image",
                    f"{img} → {wf}",
                    (
                        "Waiting for LS Submit."
                        if is_ls
                        else "Waiting for CVAT Save + job completed."
                    ),
                )
            )
    elif action == "pull":
        plat = data.get("external_platform") or "cvat"
        is_ls = plat == "label_studio"
        plat_label = "LS" if is_ls else "CVAT"
        for item in data.get("images", []):
            img = short_id(item.get("citizen_ai_image_id"))
            if item.get("skipped"):
                changes.append(
                    entry(
                        "Annotation",
                        f"Skipped {img} (already imported)",
                        "Active annotation already present.",
                    )
                )
            elif is_ls:
                decision = item.get("triage_decision") or "triaged"
                changes.append(
                    entry(
                        "Annotation",
                        f"LS import · {img} · {decision}",
                        "JSON labels stored on Annotation row.",
                        how="Via LS REST API",
                        how_detail="Fetched remote annotations; normalized to payload.",
                    )
                )
                changes.append(
                    entry(
                        "Image",
                        f"{img} → LS labeled ({decision})",
                        "workflow_state advanced from triage result.",
                    )
                )
            else:
                nlab = item.get("labels_count", 0)
                changes.append(
                    entry(
                        "Annotation",
                        f"CVAT import · {img} · {nlab} label(s)",
                        "COCO labels stored on Annotation row.",
                        how="Via CVAT REST API",
                        how_detail="Exported COCO; normalized to payload.",
                    )
                )
                changes.append(
                    entry(
                        "Image",
                        f"{img} → annotated",
                        "Marked CVAT-labeled.",
                    )
                )
        if data.get("created", 0) or data.get("images"):
            task_status = "triaged" if is_ls else "annotated"
            changes.append(
                entry(
                    "Task",
                    f"→ status {task_status}",
                    f"{plat_label} task status updated after import.",
                )
            )
    elif action == "status":
        changes.append(
            entry(
                "Snapshot",
                "Status refreshed",
                "Read-only compare of local state vs platform.",
            )
        )
    elif action == "register_webhook":
        changes.append(
            entry(
                "Webhook",
                "Registered on platform",
                "Orchestrator will auto-pull on completed annotation events.",
                how="Via platform REST API",
                how_detail="Webhook subscription created/updated.",
            )
        )
    elif action == "webhook":
        plat = data.get("platform") or (
            "label_studio" if data.get("ls_task_id") else "cvat"
        )
        is_ls = plat == "label_studio"
        plat_label = "LS" if is_ls else "CVAT"
        if data.get("action") == "auto_pull":
            pull = data.get("pull") or {}
            labels = pull.get("total_labels", 0)
            from_poller = data.get("source") == "poller"
            event = data.get("event") or ("job completed" if not is_ls else "annotation")
            changes.append(
                entry(
                    "Webhook",
                    f"{plat_label} {event} → auto-pull ({labels} label(s))",
                    (
                        "Poller fallback saw completion; same import path as webhook."
                        if from_poller
                        else "Platform notified Orchestrator; import started."
                    ),
                    how=(
                        f"Via {plat_label} REST API (poller)"
                        if from_poller
                        else f"Via {plat_label} webhook + REST pull"
                    ),
                    how_detail=(
                        "Detected completed job; exported + normalized."
                        if not is_ls
                        else "Fetched annotations; normalized to payload."
                    ),
                )
            )
            for item in pull.get("images", []):
                img = short_id(item.get("citizen_ai_image_id"))
                if item.get("skipped"):
                    changes.append(
                        entry(
                            "Annotation",
                            f"Skipped {img} (already imported)",
                            "Active annotation already present.",
                        )
                    )
                elif is_ls:
                    decision = item.get("triage_decision") or "triaged"
                    names = item.get("label_names") or []
                    label_bit = ", ".join(names) if names else "empty"
                    changes.append(
                        entry(
                            "Annotation",
                            f"LS · {img} · {decision}",
                            f"Stored labels: {label_bit}.",
                            how="Via LS REST API",
                            how_detail="Normalized remote annotation → Annotation row.",
                        )
                    )
                    changes.append(
                        entry(
                            "Image",
                            f"{img} → LS labeled ({decision})",
                            "workflow_state from triage decision.",
                        )
                    )
                else:
                    nlab = item.get("labels_count", 0)
                    changes.append(
                        entry(
                            "Annotation",
                            f"CVAT · {img} · {nlab} bbox(es)",
                            "COCO import stored on Annotation row.",
                            how="Via CVAT REST API",
                            how_detail="COCO export normalized → Annotation row.",
                        )
                    )
                    changes.append(
                        entry(
                            "Image",
                            f"{img} → annotated",
                            "Marked CVAT-labeled.",
                        )
                    )
            if pull.get("created", 0) or pull.get("images"):
                task_status = "triaged" if is_ls else "annotated"
                changes.append(
                    entry(
                        "Task",
                        f"→ status {task_status}",
                        f"{plat_label} task marked {task_status}.",
                    )
                )
        else:
            changes.append(
                entry(
                    "Webhook",
                    f"{plat_label} {data.get('event')} — {data.get('action')}",
                    data.get("reason") or "Ignored (not an import trigger).",
                )
            )
    return changes


def demo_state(
    settings: PocSettings | None = None,
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    settings = settings or PocSettings()
    plat = normalize_platform(platform or settings.platform)
    task_ref = active_demo_task_ref(settings)
    next_ref = next_demo_task_ref(settings)
    status = get_status(task_ref=task_ref, settings=settings)
    # If active ref has no row yet, status is empty — that's fine pre-push.
    task_entry = status["tasks"][0] if status["tasks"] else None
    image = _db(settings).get_image_by_citizen_id(DEMO_IMAGE_ID)
    webhook_info: dict[str, Any] | None = None
    try:
        if plat == "label_studio":
            listed = list_ls_webhooks(settings=settings)
            match = listed.get("webhooks") or []
            if match:
                wh = match[0]
                webhook_info = {
                    "id": wh.get("id"),
                    "target_url": wh.get("url"),
                    "events": wh.get("actions"),
                    "is_active": True,
                    "platform": LS_PLATFORM,
                }
        else:
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
                    "platform": "cvat",
                }
    except Exception:  # noqa: BLE001 - demo helper; platform may be down
        webhook_info = None
    return {
        "demo": {
            "image_id": DEMO_IMAGE_ID,
            "image_path": str(DEMO_IMAGE_PATH),
            "task_ref": task_entry["task"]["task_ref"] if task_entry else next_ref,
            "next_task_ref": next_ref,
            "metadata": DEMO_METADATA,
            "webhook_target": (
                DEFAULT_LS_WEBHOOK_TARGET if plat == "label_studio" else DEFAULT_WEBHOOK_TARGET
            ),
            "platform": plat,
        },
        "db_exists": settings.db_path.is_file(),
        "image_registered": image is not None,
        "task": task_entry,
        "webhook": webhook_info,
        "webhook_feed": get_webhook_feed(),
        "platform": plat,
    }
