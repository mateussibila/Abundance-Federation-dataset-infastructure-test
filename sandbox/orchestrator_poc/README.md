# Orchestrator POC (CVAT + Label Studio adapters)

Minimal proof-of-concept: validated `Image` → annotation platform → export → normalized `Annotation` in SQLite.

| Platform | Local UI |
|----------|----------|
| **CVAT** | http://localhost:8080 |
| **Label Studio** | http://localhost:8081 |
| **Orchestrator demo** | http://127.0.0.1:5050 |

Design notes: [orchestrator-poc-cvat.md](../../docs/orchestrator/orchestrator-poc-cvat.md) · test summary: [poc-local-test-summary.md](../../docs/orchestrator/poc-local-test-summary.md)

---

## Prerequisites

Install on your Mac (or Linux) before running anything:

1. **Git**
2. **Python 3.11+** (`python3 --version`)
3. **Docker Desktop** (or Docker Engine + Compose v2) — must be **running** before `docker compose …`
4. **(Optional, CVAT path)** A local [CVAT](https://github.com/cvat-ai/cvat) Community install via Docker Compose, typically under something like `~/cvat_sandbox/cvat`, listening on port **8080**. Create a superuser and note username/password.

No other system packages are required; Python deps are only `requests` and `flask` (see `requirements.txt`).

---

## One-time setup

```bash
git clone https://github.com/mateussibila/Abundance-Federation-dataset-infastructure-test.git
cd Abundance-Federation-dataset-infastructure-test/sandbox

# Config from examples (never commit the real files — they are gitignored)
cp kobo-sandbox-info.json.example kobo-sandbox-info.json          # optional: Kobo pull
cp label-studio-sandbox-info.example.json label-studio-sandbox-info.json
cp cvat-sandbox-info.json.example cvat-sandbox-info.json            # if using CVAT
# Edit the JSON files: tokens, passwords, asset_uid, etc.

cd orchestrator_poc
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python orchestrator_poc.py init-db
```

---

## Run Label Studio (port 8081)

```bash
cd ../   # sandbox/
docker compose -f docker-compose.label-studio.yml up -d
python3 scripts/bootstrap_label_studio.py
# writes api_token + project_id into label-studio-sandbox-info.json
# UI: http://localhost:8081
```

## Run the demo UI

```bash
cd orchestrator_poc
source .venv/bin/activate
python demo_app.py
# open http://127.0.0.1:5050 — select Platform: CVAT or Label Studio
```

### Optional: Orchestrator in Docker

Needs Label Studio (and CVAT, if you use that path) networks already up:

```bash
cd sandbox
docker compose -f docker-compose.label-studio.yml up -d
# CVAT must already be running (creates cvat_cvat network)
docker compose -f docker-compose.orchestrator.yml up -d --build
```

---

## Minimum test (Label Studio only)

CVAT is optional for a first pass.

1. Start Docker Desktop → Label Studio compose → bootstrap → `demo_app.py`
2. In the demo UI: **Initialize** → **Register image** → **Push** (platform = Label Studio)
3. Open the LS task, submit a triage label (`valid_weed` / `invalid` / `unsure`)
4. Confirm the Orchestrator pulls the annotation (webhook and/or poller)
5. **Reset all** clears SQLite + LS tasks for the selected platform

### With CVAT

Same flow with platform = CVAT; complete the job in CVAT UI; Orchestrator imports COCO.

### With Kobo

Fill `kobo-sandbox-info.json`, then use **Pull from Kobo → LS** in the demo UI.

---

## Demo flow (full)

1. **Initialize** — SQLite + register webhook for the selected platform  
2. **Register image** — local `images` row  
3. **Push** — creates task on CVAT or Label Studio  
4. **Open** — annotate in the platform UI  
5. Auto-pull on completed / submitted annotation (webhook + poller)  
6. **Reset all** — wipe SQLite + delete tasks on the **selected platform only**

Pipeline sketch: Kobo pull → LS triage → (manual) Push to CVAT → COCO import.

---

## Files

| Path | Role |
|------|------|
| `demo_app.py` | Flask UI |
| `poc_service.py` | Business logic (Kobo→LS→CVAT gate) |
| `kobo_adapter.py` | Kobo REST pull + media download |
| `cvat_adapter.py` | CVAT REST |
| `label_studio_adapter.py` | Label Studio REST (triage Choices) |
| `normalize_coco.py` / `normalize_ls.py` | Export → canonical payload |
| `../kobo-sandbox-info.json` | Kobo URL + token + asset (**gitignored**; use `.example`) |
| `../label-studio-sandbox-info.json` | LS URL + token + project (**gitignored**) |
| `../cvat-sandbox-info.json` | CVAT credentials (**gitignored**) |
| `../docker-compose.label-studio.yml` | LS on :8081 |

---

## Notes / known limits

- On Docker Desktop for Mac, CVAT webhooks to private IPs often fail; the demo **poller** (~4s) is a local workaround. Expect real webhooks on a normal VM network.
- Do not commit `*-sandbox-info.json`, `*.db`, `label_studio_data/`, or `kobo_media/`.
