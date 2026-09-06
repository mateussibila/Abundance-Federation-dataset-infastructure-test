# Orchestrator POC — notes

How to **run** the POC: [`sandbox/orchestrator_poc/README.md`](../sandbox/orchestrator_poc/README.md)

Official product architecture (data model, end-to-end flow, future Django stack): org repo [`docs/orchestrator/`](https://github.com/Abundance-Federation/dataset-infastructure/tree/main/docs/orchestrator)

---

## What this POC demonstrated

The Orchestrator can talk to **Kobo** (API pull), **Label Studio**, and **CVAT** over REST, push images, pull annotations back (webhook and/or local poller), and store a normalized `Annotation` in SQLite. Label Studio is used for triage; CVAT for detailed geometry (COCO export).

### Local stack

| Layer | Choice |
|-------|--------|
| Orchestrator POC | Python 3 + Flask demo UI, SQLite, `requests` |
| CVAT | Community 2.x (Docker Compose) |
| Label Studio | Community (`heartexlabs/label-studio`, Docker) |
| Export | CVAT → COCO 1.0 normalize; LS → JSON → same canonical `Annotation.payload` |

Default local ports when using this repo’s Compose/demo as written: Orchestrator `5050`, CVAT `8080`, Label Studio `8081` (change bindings if needed).

### What was exercised

| Platform | Push | Annotate | Pull back |
|----------|------|----------|-----------|
| CVAT | Create task + upload image | Bbox in UI → job completed | COCO → normalized `Annotation` |
| Label Studio | Ensure project + import | Submit triage Choices | JSON → same payload shape |

Also: activity log, entity summary flows, webhook path + poller fallback, optional Kobo → LS pull then manual push to CVAT.

### Cloud next (same adapters)

- Orchestrator can share the existing Label Studio VM; CVAT prefers its own box (~4 vCPU / 8 GB).
- HTTPS (Caddy), public webhook URLs, firewall 80/443.
- On a laptop with Docker Desktop, webhooks to private IPs often fail — the demo poller is a local workaround; prefer real webhooks on a VM network.

### Limits / open points

- Community editions: multi-user on one stack; cap is VM resources, not seat licenses.
- Authless Kindness deep links: not proven (sandbox uses normal login).
- POC assumes one Task per Image (multi-annotator concurrency still open).
- Still deferred: Kindness CaptureSession handoff; auto LS→CVAT gate by `valid_weed`.

---

## Design choices (annotation payload)

### Export format

| Format | Verdict |
|--------|---------|
| COCO 1.0 | Primary — bbox + polygon, maps cleanly to `Annotation.payload` |
| CVAT for images 1.1 | Optional raw artifact for CVAT round-trip only |
| YOLO | Defer unless training stack is YOLO-first |

Flow: export → save raw → normalize → store canonical payload.

### What goes to CVAT

| Field | Sent? |
|-------|-------|
| Image file, filename, task name, labels | yes |
| farm, volunteer, GPS, notes | no — stays in Orchestrator `images.metadata_json` |

Round-trip key: `citizen_ai_image_id` = uploaded filename stem.

### Canonical payload (sketch)

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

SQLite tables in the POC: `images`, `tasks`, `task_images`, `annotations` — see `sandbox/orchestrator_poc/db.py`. Implementation details (push/pull, CLI) live in the code and the run README.

---

## Future production-shaped stack

Not implemented in this sandbox. Target direction: Django + Postgres + object storage + Celery on Hetzner, with Kindness / Kobo / Label Studio as external systems. See the org orchestrator docs for the current data model and flow diagrams.
