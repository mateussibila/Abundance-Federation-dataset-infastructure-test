# Orchestrator POC — CVAT adapter (sketch)

**Status:** Design + code sketch for local sandbox  
**Scope:** 1 image → CVAT → COCO export → `Annotation` in SQLite

---

## Export format: use COCO 1.0

| Format | Verdict |
|--------|---------|
| **COCO 1.0** | **Primary.** Bbox + polygon segmentation, ML ecosystem standard, maps cleanly to `Annotation.payload`. |
| CVAT for images 1.1 | Optional raw artifact for CVAT round-trip only. |
| YOLO / Ultralytics YOLO | Defer unless training stack is YOLO-first. Poor fit for polygon weed outlines + rich metadata. |

**POC flow:** CVAT export `COCO 1.0` → save raw zip/json → normalize → store canonical payload.

---

## Minimum metadata to CVAT

| Field | Sent to CVAT? | Example |
|-------|---------------|---------|
| Image file | yes | bytes |
| Filename | yes | `CAI-WP01-IMG-000001.jpg` |
| Task name | yes | `TASK-0001` |
| Labels | yes | `weed`, `unknown`, `unclassifiable` |
| farm, volunteer, GPS, notes | **no** | stays in Orchestrator `images.metadata_json` |

Round-trip key: **`citizen_ai_image_id` = uploaded filename stem**.

---

## SQLite schema (4 tables)

```sql
CREATE TABLE images (
    id TEXT PRIMARY KEY,
    citizen_ai_image_id TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL,
    workflow_state TEXT NOT NULL DEFAULT 'validated',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE tasks (
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

CREATE TABLE task_images (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    image_id TEXT NOT NULL REFERENCES images(id),
    PRIMARY KEY (task_id, image_id)
);

CREATE TABLE annotations (
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
```

---

## Normalization (COCO → internal payload)

**Input:** COCO 1.0 JSON (often inside export zip)

**Output:** stable `Annotation.payload`:

```json
{
  "schema_version": "1",
  "source_platform": "cvat",
  "export_format": "COCO 1.0",
  "external_task_id": "4",
  "image_ref": "CAI-WP01-IMG-000001",
  "labels": [
    {
      "name": "weed",
      "geometry_type": "bbox",
      "points": [[120, 80, 300, 260]]
    }
  ]
}
```

**Rules:**
1. Match COCO `images[].file_name` → `citizen_ai_image_id` (strip extension)
2. Map `category_id` → label name via `categories[]`
3. `bbox [x,y,w,h]` → `points [[x,y,x+w,y+h]]`, `geometry_type=bbox`
4. `segmentation` polygon → list of `[x,y]` pairs, `geometry_type=polygon`
5. Save raw export path alongside normalized JSON

---

## Module layout

```
sandbox/orchestrator_poc/
  README.md
  requirements.txt          # requests
  orchestrator_poc.py       # CLI entrypoint
  db.py                     # SQLite CRUD
  cvat_adapter.py           # login, push, export, poll
  normalize_coco.py         # COCO → payload
  poc.db                    # created by init-db
  exports/raw/
  exports/normalized/
```

---

## CVAT adapter operations

### `push(image, task_ref, project_id, labels)`

1. `POST /api/auth/login`
2. `POST /api/tasks` with `name=task_ref`, `project_id`
3. `POST /api/tasks/{id}/data` with headers `Upload-Start` + `Upload-Finish`
4. Multipart files as `client_files[0]` (indexed keys — required on M1 sandbox)
5. Persist `Task.external_task_id`, `Task.handoff_url`, `TaskImage` row
6. Set `Image.workflow_state = queued_for_annotation`

### `pull(task_ref)`

1. Resolve `external_task_id` from SQLite
2. `POST /api/tasks/{id}/dataset/export?format=COCO%201.0&save_images=false`
3. Poll `GET /api/requests/{rq_id}` until `finished`
4. Download `result_url` (zip), extract `*.json`
5. Normalize → insert `Annotation`, set `Image.workflow_state = annotated`
6. Idempotent: skip if active annotation already exists for `(task_id, image_id)`

---

## CLI

```bash
python orchestrator_poc.py init-db

python orchestrator_poc.py seed-image \
  --image-id CAI-WP01-IMG-000001 \
  --path ../cvat-dummy-images/sandbox_farmA_vol001_20250713_topdown_01.jpg

python orchestrator_poc.py push --image-id CAI-WP01-IMG-000001 --task-ref TASK-0001

# annotate manually at handoff_url

python orchestrator_poc.py pull --task-ref TASK-0001
python orchestrator_poc.py status --task-ref TASK-0001
```

Config defaults from `../cvat-sandbox-info.json`:
- `base_url`, `username`, `password`, `project_id`

---

## POC acceptance criteria

- [ ] `push` creates CVAT task with 1 image; filename = `citizen_ai_image_id.jpg`
- [ ] Manual annotation in CVAT UI
- [ ] `pull` downloads COCO export and writes 1 `Annotation` row
- [ ] Re-running `pull` does not duplicate annotation
- [ ] `status` shows Orchestrator + CVAT IDs and workflow states

---

## Next after POC

- Wire real `ingest.py` outputs (`Processed/` + `master_tracking.csv`) as `seed-image` source
- Add Label Studio adapter with same normalized payload schema
- Replace SQLite with Django models from [data-model-v0.3.md](data-model-v0.3.md)
