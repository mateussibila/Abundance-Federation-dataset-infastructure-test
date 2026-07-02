# Orchestrator — Recommended Stack (by Layer)

**Status:** Draft for team discussion — not a final architecture decision  
**Context:** Citizen AI Dataset Orchestrator control plane (Django + Postgres + object storage)

---

## Core principle

> **PostgreSQL = metadata and workflow state** · **Object storage = image bytes (JPEG)**

The Orchestrator is the **control plane** for dataset production: API, business rules, image state, and integrations with Kindness, Kobo, and Label Studio. It is **not** the volunteer-facing home — that role belongs to Kindness.

---

## Stack by layer

| Layer | Technology | Role |
|-------|------------|------|
| **Cloud** | Hetzner Cloud (VM) | Linux server running 24/7 in the EU |
| **Containers** | Docker + Compose | Isolates Postgres, Redis, Django, and Celery on one VM |
| **Application** | **Django** + **Gunicorn** | REST API, Admin UI, webhooks, business rules |
| **Reverse proxy** | **Caddy** (or Nginx) | HTTPS, TLS certificates, domain routing — already used in Pilot 01 for Label Studio |
| **Database** | **PostgreSQL** | `Image`, `Submission`, `Task`, `Event`, lifecycle states |
| **Job queue** | **Redis** | Message broker for Celery |
| **Background workers** | **Celery worker** + **Celery beat** | Kobo import, P0 validation, S3 upload, scheduled polls |
| **Storage client** | **boto3** | S3 API client for the object storage bucket |
| **Object storage** | **Hetzner Object Storage** (pilot) | Canonical JPEG files — managed service, separate from the VM |
| **Ingestion (evolution)** | `ingest.py` → Django management command / Celery task | Same P0-1…P0-10 validation logic, running inside the Orchestrator |

---

## External systems (not part of the Orchestrator stack)

| System | Role |
|--------|------|
| **Kindness** | Volunteer identity, quests, level display (consumes Orchestrator data) |
| **KoboToolbox** | Field capture (photos + metadata) |
| **Label Studio** | Annotation — **separate VM** (already being set up in Pilot 01) |
| **CVAT** | Phase 1 spike — not yet decided for September delivery |

---

## Related documents

- `orchestrator-infra-discussion-summary.md` — full infrastructure notes (storage, upload flow, architecture diagram)
- `data-model-v0.1.md` — entity model (when added to this repo)
- Org repo: `Abundance-Federation/dataset-infastructure` → `docs/orchestrator/`

---

*This document captures a layer-by-layer stack summary for onboarding. See `decisions.md` for formal decisions.*
