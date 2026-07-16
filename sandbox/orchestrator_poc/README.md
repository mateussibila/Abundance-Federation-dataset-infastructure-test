# Orchestrator POC (CVAT adapter)

Minimal proof-of-concept: one validated `Image` → CVAT task → manual annotation → **COCO 1.0** export → normalized `Annotation` in SQLite.

Full design: [orchestrator-poc-cvat.md](../../docs/orchestrator/orchestrator-poc-cvat.md)

## Setup

```bash
cd dataset-infastructure/sandbox/orchestrator_poc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python orchestrator_poc.py init-db
```

Requires local CVAT sandbox (`../cvat-sandbox-info.json`).

## Demo UI (screen recording)

Side-by-side demo: **Orchestrator buttons on the left**, **CVAT browser on the right**.

```bash
pip install -r requirements.txt
python demo_app.py
# open http://127.0.0.1:5050
```

Default demo task: **`TASK-0002`** (fresh ref for recording; change in `poc_service.py` if needed).

Buttons map to the demo flow. Annotations import automatically when the CVAT job is marked **completed** (no manual Pull button).

## Commands (CLI)

```bash
python orchestrator_poc.py seed-image \
  --image-id CAI-WP01-IMG-000001 \
  --path ../cvat-dummy-images/sandbox_farmA_vol001_20250713_topdown_01.jpg \
  --farm farmA --volunteer-id vol001 --photo-type topdown

python orchestrator_poc.py push --image-id CAI-WP01-IMG-000001 --task-ref TASK-0001

# Annotate in browser at handoff_url, then:
python orchestrator_poc.py pull --task-ref TASK-0001
python orchestrator_poc.py status --task-ref TASK-0001
```

## Minimum metadata sent to CVAT

- Image file
- Filename (`CAI-WP01-IMG-000001.jpg`)
- Task name (`TASK-0001`)
- Labels (`weed`, `unknown`, `unclassifiable`)

All other metadata stays in Orchestrator SQLite (`images.metadata_json`).

## Export format

**COCO 1.0** — raw export saved under `exports/raw/`, normalized payload under `exports/normalized/` and in `annotations.payload_json`.

## Resume demo later

Everything needed to re-run the screen recording is in this repo. CVAT itself lives outside the repo (`~/cvat_sandbox/cvat` via Docker Compose).

### 1. Start CVAT (once per session)

```bash
open -a Docker   # if Docker Desktop is not running
cd ~/cvat_sandbox/cvat
docker compose up -d
# UI: http://localhost:8080  (admin / see ../cvat-sandbox-info.json)
```

### 2. Start Orchestrator demo UI

```bash
cd dataset-infastructure/sandbox/orchestrator_poc
source .venv/bin/activate   # or: python3 -m venv .venv && pip install -r requirements.txt
python demo_app.py
# open http://127.0.0.1:5050
```

If port 5050 is busy: `lsof -ti :5050 | xargs kill -9`

### 3. Recording layout

- **Left:** Orchestrator demo UI (`http://127.0.0.1:5050`)
- **Right:** CVAT in browser (`http://localhost:8080`)

### 4. Demo flow (buttons)

1. **Initialize** — create SQLite schema (POC only)
2. **Register image** — local `images` row (no CVAT yet)
3. **Push to CVAT** — creates task + uploads image
4. **Enable webhook** — registers CVAT `update:job` → Orchestrator (once per project)
5. **Open CVAT** — annotate, Save, then **Menu → Change job state → completed**
6. Orchestrator **auto-pulls** COCO (activity log updates within ~2s; manual Pull still available)
7. **Reset all** — wipe local SQLite + activity log **and delete all tasks** in the CVAT sandbox project

Production rule: webhook fires on many job updates, but Orchestrator only pulls when **state → completed**. Save alone does not pull.

Webhook URL (CVAT Docker → Mac): `http://host.docker.internal:5050/api/webhooks/cvat`

### 5. Fresh task ref

Default demo task refs auto-increment (`TASK-0001`, `TASK-0002`, …) from local DB + CVAT project names. After **Reset all**, the next push starts again at **`TASK-0001`**.

### Files

| Path | Role |
|------|------|
| `demo_app.py` | Flask UI for recording |
| `poc_service.py` | Business logic + activity log text |
| `cvat_adapter.py` | CVAT REST API client |
| `../cvat-sandbox-info.json` | Local CVAT URL + credentials |
| `../cvat-dummy-images/` | Sample weed-like images |
