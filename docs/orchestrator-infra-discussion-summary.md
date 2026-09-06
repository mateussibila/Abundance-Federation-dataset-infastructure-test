# Orchestrator Infrastructure — Discussion Summary

**Date:** July 2026  
**Context:** Notes from planning discussions about the Citizen AI Dataset Orchestrator, object storage, and VM stack. Written for team members without prior cloud or Django experience.

**Related documents in `docs/`:**

- `Citizen_AI_Dataset_Infrastructure_Roadmap.pdf` — long-term architecture
- `Pilot01_Tooling_Plan.pdf`, `Pilot01_Two_Week_Sprint_Plan_V1.1.pdf` — pilot tooling
- `Pipeline_Security_Roadmap.pdf` — ingestion validation (P0–P3)
- `data-model-v0.1.md` — entity model (when added to repo)

---

## 1. Repository and GitHub access

- The Cloud Agent can work on **this repo** (commits, branches, PRs) but **cannot create new private repositories** on a personal GitHub account — the integration token lacks `createRepository` permission.
- To create a new private repo: use [github.com/new](https://github.com/new) or `gh auth login` locally with your own account.

---

## 2. Sharing documents in Cursor

- PDFs upload reliably; DOCX is binary and less dependable in chat.
- Preferred approaches: paste text, add `.md`/`.txt` to the repo, export PDF, or share a link (Google Docs, Notion).
- Planning PDFs were saved under `docs/` (see list above).

---

## 3. Object storage for images (Kobo → Orchestrator)

### S3 API ≠ AWS S3

- **S3 API** is a standard protocol (buckets, keys, PUT/GET).
- **AWS S3** is Amazon’s product; **Hetzner Object Storage**, Backblaze B2, Cloudflare R2, etc. implement the same API.
- You choose **one provider** — Hetzner is not a “middleman” between you and AWS.
- Python code (`boto3`) is nearly identical; only `endpoint_url` and credentials change.

### Why not Google Drive or local disk?

- **Google Drive** — fine for the manual pilot; not canonical storage (weak API, no lifecycle/versioning for pipelines).
- **Local VM folder** — lost on redeploy, hard to share with Label Studio/CVAT, doesn’t scale.
- **Orchestrator rule:** Postgres holds metadata; **object storage holds JPEG bytes**.

### Recommended path

| Phase | Storage | Rationale |
|-------|---------|-----------|
| Pilot / Orchestrator v0 | **Hetzner Object Storage** | EU, cheap, same vendor as VM, S3-compatible |
| Operational launch (2027) | **AWS S3 `eu-west-1` (Dublin)** or keep Hetzner | Versioning, compliance, mature ops if public submissions scale |

### Proposed bucket layout (from data model v0.1)

```
citizen-ai-datasets/
├── raw/kobo/{project_id}/{submission_id}/...
├── projects/{project_slug}/images/{citizen_ai_image_id}.jpg
├── quarantine/{reason}/...
└── releases/{release_id}/...
```

Example `citizen_ai_image_id`: `CAI-WP01-000042-45deg`  
Example `storage_path`: `projects/weed-pilot-01/images/CAI-WP01-000042-45deg.jpg`

### Hetzner cost estimate (pilot, excl. VAT)

| Item | Approx. monthly |
|------|-----------------|
| Object Storage base | €4.99–6.49 (includes **1 TB** storage + ~1 TB egress) |
| VM Orchestrator (CX33) | €6.49–8.49 |
| VM Label Studio (CX22) | ~€4.49 |
| **Total infra** | **~€12–16/month** |

~500–6,000 images at ~2.5 MB each ≈ 1.5–18 GB — well within included storage. Confirm current prices in the Hetzner console.

---

## 4. Data model v0.1 (key points)

1. **`Image`** is the canonical unit — one photo = one `citizen_ai_image_id`.
2. **`Annotation`** is append-only; rework creates a new row.
3. **`Event`** is audit-only; operational state lives in domain tables.
4. **`KoboFormConfig`** maps `kobo_form_uid` → `Project` (required before capture).
5. **Contributor identity from Kindness** — not Kobo `Volunteer_ID` dropdown.
6. **UUID PKs** internally; human-readable ID only on `citizen_ai_image_id`.

**Kobo flow:** `CaptureSession` (token in URL) → `Submission` → one or more `Image` rows (e.g. 45° + topdown).

**Image lifecycle:** `pending_import` → `captured` → `validated` | `quarantined` → `assigned` → `annotated` → `under_review` → `reviewed` → `released`.

---

## 5. Infrastructure stack — what each piece is

| Component | Type | Role |
|-----------|------|------|
| **VM (Hetzner)** | Cloud server | Linux machine running 24/7 |
| **Docker** | Container runtime | Runs Postgres, Redis, Django, Celery isolated on one VM |
| **Django** | Python web framework | Orchestrator app: API, Admin, business rules, webhooks |
| **Gunicorn** | Python WSGI server | Keeps Django serving HTTP in production |
| **Caddy / Nginx** | Reverse proxy | HTTPS, domain, TLS certificates |
| **PostgreSQL** | Database server (not Python) | `Image`, `Task`, `Submission`, states, events |
| **Redis** | In-memory server (not Python) | Job queue for Celery |
| **Celery worker** | Python worker process | Heavy work: Kobo import, validation, S3 upload |
| **Celery beat** | Python scheduler | Cron-like jobs (e.g. poll Kobo every 5 min) |
| **boto3** | Python library | S3 API client for Hetzner bucket uploads/downloads |
| **Hetzner Object Storage** | Managed service (separate from VM) | JPEG files |

**Django is not a desktop app.** It is Python code that needs Python, Gunicorn, Postgres, and (recommended) Redis + Celery.

### Why Docker with separate containers?

- Isolation, reproducible deploys, fixed versions, easier install.
- Containers on the **same VM** talk over an internal Docker network (`db:5432`, `redis:6379`).
- Not mandatory — bare-metal install works too; Docker is convenience for a small team.

### Do you need pgAdmin on the VM?

**No.** Postgres runs without a UI. Django Admin (`/admin/`) is enough for day-to-day operations. pgAdmin (or DBeaver) is **optional** for developers inspecting raw SQL — preferably on a laptop via SSH tunnel, not exposed on the public internet.

### Caddy vs Nginx

Both are reverse proxies in front of Gunicorn. **Caddy** — automatic HTTPS, simpler config (used in Pilot 01 for Label Studio). **Nginx** — more common in production. Equivalent role for the Orchestrator.

---

## 6. Architecture diagram (pilot)

```
[Browser] ──HTTPS──► VM: Caddy → Gunicorn → Django
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                 Postgres   Redis    Celery worker
                              │         │
                              │         ├──► boto3 → Hetzner bucket
                              │         └──► Kobo API
                              │
                    Celery beat (scheduled polls)

External:
  KoboToolbox (capture)
  Kindness Platform (identity, handoff)
  Label Studio / CVAT (annotation, separate VM)
  Hetzner Object Storage (files only)
```

---

## 7. Who handles Kindness, Kobo, Label Studio?

| Source | Pattern | Handler |
|--------|---------|---------|
| **Kindness** | HTTP POST to Orchestrator API | **Django** (views) |
| **Label Studio** | Webhook POST | **Django** responds fast; may enqueue **Celery** |
| **Kobo** | Poll API on schedule (or webhook if available) | **Celery beat** + **Celery worker** |

Django = HTTP entry point + Admin. Celery = slow/repetitive work without blocking web requests.

---

## 8. Kobo submission → VM timeline (simulated)

Volunteer submits on phone → Kobo stores submission in **~3–8 s**. VM does nothing until poll or webhook.

**With Celery beat polling every 5 minutes:**

| Time (from poll start) | Action |
|------------------------|--------|
| 0.0 s | Beat enqueues `poll_kobo_submissions` on Redis |
| 0.2 s | Worker fetches new submissions from Kobo API |
| 0.5 s | Download attachments to VM memory |
| 2–8 s | P0 validation per image (size, format, EXIF strip, SHA-256) |
| 8–10 s | `boto3 put_object` → Hetzner bucket (× N photos) |
| 10–11 s | `INSERT Image` + `Event` in Postgres; `Submission` → validated |
| 11–15 s | Optional: enqueue task creation for Label Studio |

**Webhook variant:** Django receives POST → returns 200 immediately → Celery runs the same import pipeline.

Maria submits at 14:00:00 → Admin shows images at ~14:05:15 with 5-minute polling (or ~14:01:15 with 1-minute polling).

---

## 9. Bucket confirmation — does storage notify Django?

**No.** Object storage is passive.

1. Celery calls `boto3.put_object(...)`.
2. Bucket responds **HTTP 200** with `ETag` — that **is** the confirmation.
3. Only then insert/update `Image` in Postgres.

There is no separate “bucket calls Django” step. Optional: `head_object` to double-check existence. Event notifications (AWS-style) are not needed for the pilot.

**Recommended order:** validate → upload → Postgres record → `Event`.

---

## 10. Access and operations

| Need | How |
|------|-----|
| Deploy / logs | SSH to VM |
| Admin UI (tasks, images, states) | Browser → `https://orchestrator.<domain>/admin/` |
| Local development | `python manage.py runserver` on laptop; Postgres/Redis via Docker |
| Public webhooks | Require VM (or tunnel); laptop alone is not enough for 24/7 |

Django runs **24/7** on the VM via Gunicorn. Postgres, Redis, Celery worker, and Celery beat run continuously as well.

---

## 11. Build roadmap (start to finish)

1. **Accounts:** Hetzner Cloud + Object Storage bucket + access keys.
2. **VM:** Ubuntu CX33, Docker + Docker Compose, firewall, domain + Caddy.
3. **Django project:** models per data model v0.1, migrations, REST API, Admin.
4. **docker-compose.yml:** `db`, `redis`, `web`, `worker`, `beat`.
5. **Ingestion:** Celery task — Kobo API → P0 validation → `put_object` → Postgres.
6. **Integrations:** Kindness (`CaptureSession`, contributors), Label Studio (tasks, webhooks).
7. **Ops:** Postgres backups, audit logs (P0), runbook.

---

## 12. Glossary

| Term | One-line definition |
|------|---------------------|
| **S3-compatible** | Speaks the same API as Amazon S3 |
| **Bucket** | Top-level container for objects (files) |
| **boto3** | Python library to call S3 API |
| **Reverse proxy** | Caddy/Nginx — HTTPS in front of Django |
| **Webhook** | External system POSTs to your URL when something happens |
| **Polling** | Your worker asks Kobo API periodically for new data |
| **Idempotent** | Running import twice does not duplicate rows (check `kobo_submission_id`) |

---

## 13. Guiding principles (from project docs)

- **Own the workflow.** Orchestrator is source of truth for state.
- **Reuse tools** (Kobo, Label Studio/CVAT) but do not let them own workflow state.
- **Train non-experts** through a progression path (L1–L8 / apprenticeship model).
- **Automate only what pilots prove** is worth automating.
- **Logging before blocking** (Security Roadmap).

---

*This document captures discussion notes for onboarding. It is not a final architecture decision record — see `decisions.md` for formal decisions.*
