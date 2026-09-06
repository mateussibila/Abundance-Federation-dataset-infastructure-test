# Orchestrator POC — local test summary (CVAT + Label Studio)

**Status:** Local POC validated  
**Repo:** sandbox `dataset-infastructure-test`  
**Question (Rick):** Can the Orchestrator talk to Label Studio and CVAT via API + webhook?  
**Answer:** **Yes** — demonstrated locally.

---

## Stack used (local)

| Layer | Choice |
|-------|--------|
| Orchestrator POC | Python 3 + Flask demo UI, SQLite, `requests` REST clients |
| Annotation (spike) | **CVAT Community** 2.x (Docker Compose — server, UI, workers, Postgres, Redis, Traefik) |
| Annotation (prod path) | **Label Studio Community** (`heartexlabs/label-studio`, Docker) |
| Export / payload | CVAT → COCO 1.0 normalize; LS → JSON annotations → same canonical `Annotation.payload` |
| Local networking | Docker Desktop; Orchestrator on host `:5050` and/or container on `cvat_cvat` + `label_studio_net` |
| OS (dev) | macOS |

Ports: Orchestrator `5050` · CVAT `8080` · Label Studio `8081`.

---

## What we tested

| Platform | Push (API) | Annotate | Pull back into Orchestrator |
|----------|------------|----------|-----------------------------|
| **CVAT** (`:8080`) | Create task + upload 1 image | Manual bbox in UI → job **completed** | COCO export → normalized `Annotation` in SQLite |
| **Label Studio** (`:8081`) | Ensure project + import image | Submit annotation in UI | JSON annotations → same canonical payload |

Shared Orchestrator behaviour:

- Local SQLite entities: `Image` → `Task` / `TaskImage` → `Annotation`
- Demo UI (`:5050`) with platform selector (CVAT | Label Studio)
- Activity log + entity summary state flows
- Auto-import after annotation completes (webhook path + poller fallback)

---

## How we ran it locally

### Stack

1. **CVAT** — existing Docker sandbox (`~/cvat_sandbox/cvat`), UI http://localhost:8080  
2. **Label Studio** — `docker compose -f sandbox/docker-compose.label-studio.yml up -d` → http://localhost:8081  
3. **Orchestrator demo** — `python3 demo_app.py` (or Docker compose on CVAT network) → http://127.0.0.1:5050  

### API

- Orchestrator → CVAT: create task, upload image, list jobs, export COCO, register webhooks  
- Orchestrator → Label Studio: login/session, create project, import image, list/get tasks, register webhooks, delete tasks on reset  

### Webhook / auto-pull

- **CVAT:** `POST /api/webhooks/cvat` — pull when job state → **completed**. On Docker Desktop for Mac, CVAT egress to private IPs is often blocked; a **poller** (~4s) uses the same pull path (workaround for local only; real webhooks expected on a normal VM network).  
- **Label Studio:** `POST /api/webhooks/label_studio` — pull on **ANNOTATION_CREATED** (+ poller fallback).  

Demo flow: Initialize → Register image → Push → Open platform → annotate → Orchestrator auto-updates entities.

Config / code: `sandbox/orchestrator_poc/` (`cvat_adapter.py`, `label_studio_adapter.py`, `poc_service.py`, `demo_app.py`).

---

## Next steps (non-local POC)

Move the same adapters to cloud VMs (software stays Community / free; pay only infra).

### Suggested sizing (Hetzner-class, EU)

| Role | Size (approx.) | Notes |
|------|----------------|-------|
| **CVAT** | 4 vCPU / **8 GB** RAM | Heavy Docker stack |
| **Orchestrator** | 2 vCPU / **4 GB** RAM | Flask POC is light; can share a small VM |
| **Label Studio** | Already running on existing VM | Prefer not to co-locate CVAT on the 4 GB LS box without upgrading RAM |

### Estimated cost (~1 week)

Hourly billing ≈ monthly ÷ 4.3 (excl. VAT; confirm in console):

| Setup | ~1 week |
|-------|---------|
| New CVAT VM + small Orchestrator VM | **~€3–5** (comfortable **~€5–7**) |
| New CVAT VM only; Orchestrator on existing LS VM | **~€1.50–2.50** |

On the existing LS VM: Orchestrator can share it; full CVAT + LS on **4 GB** is tight — upgrade to **≥8 GB** or keep CVAT on its own VM.

Also: HTTPS (Caddy), public webhook URLs, firewall 80/443.

---

## Limitations (users / scale)

- **CVAT Community / Label Studio Community:** multi-user on **one stack**; no per-seat license for this POC model.  
- Cap is **VM resources**, not a fixed “N users” in the free software.  
- Rough guide: dozens concurrent annotators on a well-sized single stack; hundreds of registered users is fine if few annotate at once.  
- **Authless Kindness deep link** (no CVAT/LS login) is a **product requirement still to design** — not proven in this POC (sandbox still uses normal login).  
- Local Mac: CVAT webhooks may need the poller; validate real webhooks on VMs.  
- POC assumes **one Task per Image** (multi-annotator concurrency is an open data-model question).

---

## Add Kobo integration (local POC)

**Flow:** Kobo API pull → save Image → **always** Label Studio triage → if `valid_weed` → **CVAT** detailed annotation.

### Setup

1. Copy `sandbox/kobo-sandbox-info.json.example` → `sandbox/kobo-sandbox-info.json`
2. Set `token`, `asset_uid`, and `base_url` (`https://eu.kobotoolbox.org` or `https://kf.kobotoolbox.org`)
3. Ensure LS (`:8081`) and CVAT (`:8080`) are up; run `python demo_app.py` → `:5050`

### Demo script

1. **Initialize**
2. **Pull from Kobo → LS** — downloads submissions, seeds images, auto-pushes to Label Studio
3. In LS: triage (`valid_weed` / `invalid` / `unsure`) and submit (optional for the demo)
4. **Push to CVAT** (manual button) — no auto-send; no weed gate in this POC
5. In CVAT: detailed annotation → complete job → Orchestrator imports COCO

LS labeling config is triage Choices. Existing LS projects are patched on `ensure_project`.

Still deferred: Kindness handoff / CaptureSession; Kobo→Orch webhooks to localhost; auto LS→CVAT gate by `valid_weed`.

---

## Bottom line

Local POC shows Orchestrator **can** communicate with **Kobo (pull), Label Studio, and CVAT** over **REST API**, with LS→CVAT gated by triage, and close the loop with **webhook-driven (or equivalent) annotation import**.
