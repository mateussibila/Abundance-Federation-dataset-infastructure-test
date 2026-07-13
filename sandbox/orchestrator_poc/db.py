"""SQLite persistence for the Orchestrator CVAT POC."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    citizen_ai_image_id TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL,
    workflow_state TEXT NOT NULL DEFAULT 'validated',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    task_ref TEXT NOT NULL UNIQUE,
    task_type TEXT NOT NULL DEFAULT 'annotation',
    external_platform TEXT NOT NULL DEFAULT 'cvat',
    external_project_id TEXT,
    external_task_id TEXT,
    handoff_url TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_images (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    image_id TEXT NOT NULL REFERENCES images(id),
    PRIMARY KEY (task_id, image_id)
);

CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    image_id TEXT NOT NULL REFERENCES images(id),
    payload_json TEXT NOT NULL,
    export_format TEXT NOT NULL,
    raw_export_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (task_id, image_id, status)
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def seed_image(
        self,
        citizen_ai_image_id: str,
        storage_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        image_id = str(uuid.uuid4())
        metadata = metadata or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO images (id, citizen_ai_image_id, storage_path, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(citizen_ai_image_id) DO UPDATE SET
                    storage_path = excluded.storage_path,
                    metadata_json = excluded.metadata_json
                """,
                (image_id, citizen_ai_image_id, storage_path, json.dumps(metadata)),
            )
            row = conn.execute(
                "SELECT id FROM images WHERE citizen_ai_image_id = ?",
                (citizen_ai_image_id,),
            ).fetchone()
        return row["id"]

    def get_image_by_citizen_id(self, citizen_ai_image_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM images WHERE citizen_ai_image_id = ?",
                (citizen_ai_image_id,),
            ).fetchone()

    def get_task_by_ref(self, task_ref: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM tasks WHERE task_ref = ?",
                (task_ref,),
            ).fetchone()

    def create_task(
        self,
        task_ref: str,
        external_project_id: str,
        external_task_id: str,
        handoff_url: str,
    ) -> str:
        task_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, task_ref, external_project_id, external_task_id,
                    handoff_url, status
                ) VALUES (?, ?, ?, ?, ?, 'queued_for_annotation')
                """,
                (task_id, task_ref, external_project_id, external_task_id, handoff_url),
            )
        return task_id

    def link_task_image(self, task_id: str, image_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_images (task_id, image_id) VALUES (?, ?)",
                (task_id, image_id),
            )

    def update_image_workflow_state(self, image_id: str, workflow_state: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE images SET workflow_state = ? WHERE id = ?",
                (workflow_state, image_id),
            )

    def update_task_status(self, task_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )

    def get_task_images(self, task_id: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT i.* FROM images i
                JOIN task_images ti ON ti.image_id = i.id
                WHERE ti.task_id = ?
                """,
                (task_id,),
            ).fetchall()

    def get_active_annotation(self, task_id: str, image_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM annotations
                WHERE task_id = ? AND image_id = ? AND status = 'active'
                """,
                (task_id, image_id),
            ).fetchone()

    def clear_active_annotations(self, task_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM annotations WHERE task_id = ? AND status = 'active'",
                (task_id,),
            )

    def create_annotation(
        self,
        task_id: str,
        image_id: str,
        payload: dict[str, Any],
        export_format: str,
        raw_export_path: str | None,
    ) -> str:
        annotation_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotations (
                    id, task_id, image_id, payload_json, export_format, raw_export_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_id,
                    task_id,
                    image_id,
                    json.dumps(payload, indent=2),
                    export_format,
                    raw_export_path,
                ),
            )
        return annotation_id

    def clear_all_records(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM task_images")
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM images")

    def list_all_entities(self) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            images = [
                dict(row)
                for row in conn.execute("SELECT * FROM images ORDER BY created_at").fetchall()
            ]
            tasks = [
                dict(row)
                for row in conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
            ]
            task_images = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT ti.task_id, ti.image_id, t.task_ref, i.citizen_ai_image_id
                    FROM task_images ti
                    JOIN tasks t ON t.id = ti.task_id
                    JOIN images i ON i.id = ti.image_id
                    ORDER BY t.created_at, i.created_at
                    """
                ).fetchall()
            ]
            annotations = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT a.*, i.citizen_ai_image_id, t.task_ref
                    FROM annotations a
                    JOIN images i ON i.id = a.image_id
                    JOIN tasks t ON t.id = a.task_id
                    ORDER BY a.created_at
                    """
                ).fetchall()
            ]
        for ann in annotations:
            try:
                payload = json.loads(ann.get("payload_json") or "{}")
                ann["labels_count"] = len(payload.get("labels", []))
            except json.JSONDecodeError:
                ann["labels_count"] = 0
        return {
            "images": images,
            "tasks": tasks,
            "task_images": task_images,
            "annotations": annotations,
        }

    def status_report(self, task_ref: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if task_ref:
                tasks = conn.execute(
                    "SELECT * FROM tasks WHERE task_ref = ?",
                    (task_ref,),
                ).fetchall()
            else:
                tasks = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()

            report: list[dict[str, Any]] = []
            for task in tasks:
                images = conn.execute(
                    """
                    SELECT i.* FROM images i
                    JOIN task_images ti ON ti.image_id = i.id
                    WHERE ti.task_id = ?
                    """,
                    (task["id"],),
                ).fetchall()
                annotations = conn.execute(
                    """
                    SELECT a.*, i.citizen_ai_image_id
                    FROM annotations a
                    JOIN images i ON i.id = a.image_id
                    WHERE a.task_id = ? AND a.status = 'active'
                    """,
                    (task["id"],),
                ).fetchall()
                report.append(
                    {
                        "task": dict(task),
                        "images": [dict(row) for row in images],
                        "annotations": [dict(row) for row in annotations],
                    }
                )
            return report
