"""Static before/after activity-log copy review (non-operational)."""

from __future__ import annotations

from typing import Any

from poc_service import entity_changes_for_action

# Verbatim "before" blocks from the demo recording session (verbose copy).
# Paired with live entity_changes_for_action() output for the same actions.

_REVIEW_CASES: list[dict[str, Any]] = [
    {
        "label": "reset",
        "when": "8:40:03 PM",
        "action": "reset",
        "before": [
            {
                "entity": "Database",
                "summary": "Cleared all Orchestrator records",
                "detail": (
                    "Deleted every row from images, tasks, task_images, and annotations. "
                    "Activity feed and completed-job poller state were also reset."
                ),
            },
            {
                "entity": "CVAT",
                "summary": "Deleted 0 task(s) from sandbox project",
                "detail": (
                    "REST API DELETE /api/tasks/{id} for every task in the configured CVAT project. "
                    "No tasks found. CVAT project tasks wiped."
                ),
            },
        ],
        "data": {"cvat": {"deleted_count": 0, "deleted_task_ids": [], "errors": []}},
    },
    {
        "label": "init",
        "when": "8:40:09 PM",
        "action": "init",
        "before": [
            {
                "entity": "Database",
                "summary": "SQLite schema ready (POC setup only)",
                "detail": (
                    "Created local tables: images, tasks, task_images, annotations. "
                    "In production this happens once at deploy via Django migrations — not a user action."
                ),
            },
        ],
        "data": {},
    },
    {
        "label": "pull_kobo",
        "when": "8:40:15 PM",
        "action": "pull_kobo",
        "before": [
            {
                "entity": "Image",
                "summary": "Pulled 2 image(s) from Kobo → images table",
                "detail": (
                    "Newest-first submission ≥ start_date. Registered: CAI-KOBO-790199718-45deg, "
                    "CAI-KOBO-790199718-topdown. workflow_state=ingested — Push to LS / CVAT is a separate step."
                ),
            },
        ],
        "data": {
            "images_imported": 2,
            "imported": [
                {"image_id": "CAI-KOBO-790199718-45deg"},
                {"image_id": "CAI-KOBO-790199718-topdown"},
            ],
        },
    },
    {
        "label": "push_ls",
        "when": "8:40:21 PM",
        "action": "push_ls",
        "before": [
            {
                "entity": "Image",
                "summary": "CAI-KOBO-790199718-topdown → workflow_state in_ls_triage",
                "detail": "Waiting for Label Studio annotation (draw weed box / triage), then Submit.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": (
                    "UPDATE images SET workflow_state='in_ls_triage' for this citizen_ai_image_id. "
                    "Platform handoff already happened on the Task / TaskImage entries above."
                ),
            },
            {
                "entity": "TaskImage",
                "summary": "Linked CAI-KOBO-790199718-topdown ↔ LS-CAI-KOBO-790199718-topdown",
                "detail": "Uploaded CAI-KOBO-790199718-topdown.jpg to label_studio.",
                "how": "Same REST push — image bytes to Label Studio",
                "how_detail": (
                    "File CAI-KOBO-790199718-topdown.jpg is the request body of the import call above; "
                    "Orchestrator also inserts a TaskImage row linking local Image ↔ LS task."
                ),
            },
            {
                "entity": "Task",
                "summary": "Created LS-CAI-KOBO-790199718-topdown → label_studio #1",
                "detail": (
                    "Orchestrator created a LS triage job. "
                    "Handoff: http://localhost:8081/projects/1/data?task=1"
                ),
                "how": "Pushed via Label Studio REST API",
                "how_detail": (
                    "POST /api/projects/{id}/import — multipart upload of CAI-KOBO-790199718-topdown.jpg "
                    "(creates LS task #1). Then PATCH /api/tasks/1/ — "
                    "meta.task_ref=LS-CAI-KOBO-790199718-topdown, "
                    "meta.citizen_ai_image_id=CAI-KOBO-790199718-topdown."
                ),
            },
        ],
        "data": {
            "external_platform": "label_studio",
            "task_ref": "LS-CAI-KOBO-790199718-topdown",
            "external_task_id": "1",
            "image_id": "CAI-KOBO-790199718-topdown",
            "handoff_url": "http://localhost:8081/projects/1/data?task=1",
            "upload_filename": "CAI-KOBO-790199718-topdown.jpg",
        },
    },
    {
        "label": "webhook LS",
        "when": "8:40:48 PM",
        "action": "webhook",
        "before": [
            {
                "entity": "Task",
                "summary": "LS-CAI-KOBO-790199718-topdown → status triaged",
                "detail": "LS task marked triaged after auto-pull.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": "UPDATE tasks SET status='triaged' for this task_ref.",
            },
            {
                "entity": "Image",
                "summary": "CAI-KOBO-790199718-topdown → LS labeled (valid)",
                "detail": "Image workflow advanced after LS webhook/poller pull.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": (
                    "UPDATE images SET workflow_state from in_ls_triage → triaged_* (valid) after import."
                ),
            },
            {
                "entity": "Annotation",
                "summary": "LS labeled · triage=valid · CAI-KOBO-790199718-topdown",
                "detail": (
                    "Annotation row created from Label Studio (export_format=JSON). "
                    "Distinct from later CVAT COCO labels."
                ),
                "how": "Pulled via Label Studio REST API after webhook/poller",
                "how_detail": (
                    "GET /api/tasks/1/ → annotations[].result normalized to Orchestrator "
                    "payload.labels = [weed (bbox)]. triage='weed' → decision=valid."
                ),
            },
            {
                "entity": "Webhook",
                "summary": (
                    "LS ANNOTATION_CREATED → auto-pull LS-CAI-KOBO-790199718-topdown (triage labels=1)"
                ),
                "detail": (
                    "Label Studio webhook (ANNOTATION_CREATED / TASK_COMPLETED). "
                    "Imported LS JSON into Orchestrator — not CVAT COCO bboxes."
                ),
                "how": "Received via Label Studio webhook → then REST API pull",
                "how_detail": (
                    "LS HTTP POST → Orchestrator /api/webhooks/label_studio "
                    "(action=ANNOTATION_CREATED, task=#1). Orchestrator then GET /api/tasks/1/ "
                    "and normalized annotations[].result. Imported: CAI-KOBO-790199718-topdown: "
                    "weed (bbox); triage_decision=valid."
                ),
            },
        ],
        "data": {
            "action": "auto_pull",
            "platform": "label_studio",
            "event": "ANNOTATION_CREATED",
            "source": "webhook",
            "task_ref": "LS-CAI-KOBO-790199718-topdown",
            "ls_task_id": "1",
            "external_task_id": "1",
            "pull": {
                "total_labels": 1,
                "created": 1,
                "images": [
                    {
                        "citizen_ai_image_id": "CAI-KOBO-790199718-topdown",
                        "labels_count": 1,
                        "triage_decision": "valid",
                        "triage": "weed",
                        "label_names": ["weed"],
                        "geometries": ["bbox"],
                    }
                ],
            },
        },
    },
    {
        "label": "push_cvat",
        "when": "8:49:50 PM",
        "action": "push_cvat",
        "before": [
            {
                "entity": "Image",
                "summary": "CAI-KOBO-790199718-topdown → workflow_state in_cvat",
                "detail": "Waiting for CVAT bbox annotation → job completed.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": (
                    "UPDATE images SET workflow_state='in_cvat' for this citizen_ai_image_id. "
                    "Platform handoff already happened on the Task / TaskImage entries above."
                ),
            },
            {
                "entity": "TaskImage",
                "summary": "Linked CAI-KOBO-790199718-topdown ↔ CVAT-CAI-KOBO-790199718-topdown",
                "detail": "Uploaded CAI-KOBO-790199718-topdown.jpg to cvat.",
                "how": "Same REST push — image bytes to CVAT",
                "how_detail": (
                    "File CAI-KOBO-790199718-topdown.jpg uploaded on the task data endpoint; "
                    "Orchestrator inserts a TaskImage row linking local Image ↔ CVAT task."
                ),
            },
            {
                "entity": "Task",
                "summary": "Created CVAT-CAI-KOBO-790199718-topdown → cvat #2",
                "detail": (
                    "Orchestrator created a CVAT annotation job. "
                    "Handoff: http://localhost:8080/tasks/2"
                ),
                "how": "Pushed via CVAT REST API",
                "how_detail": (
                    "POST /api/tasks — create task #2 in the sandbox project. "
                    "Then POST /api/tasks/2/data — upload CAI-KOBO-790199718-topdown.jpg."
                ),
            },
        ],
        "data": {
            "external_platform": "cvat",
            "task_ref": "CVAT-CAI-KOBO-790199718-topdown",
            "external_task_id": "2",
            "image_id": "CAI-KOBO-790199718-topdown",
            "handoff_url": "http://localhost:8080/tasks/2",
            "upload_filename": "CAI-KOBO-790199718-topdown.jpg",
        },
    },
    {
        "label": "webhook CVAT",
        "when": "8:58:20 PM",
        "action": "webhook",
        "before": [
            {
                "entity": "Task",
                "summary": "CVAT-CAI-KOBO-790199718-topdown → status annotated",
                "detail": "CVAT task marked annotated after auto-pull.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": "UPDATE tasks SET status='annotated' for this task_ref.",
            },
            {
                "entity": "Image",
                "summary": "CAI-KOBO-790199718-topdown → workflow_state annotated",
                "detail": "Marked CVAT-labeled after webhook-triggered pull.",
                "how": "Local SQLite update only (no platform write for this row)",
                "how_detail": "UPDATE images SET workflow_state='annotated'.",
            },
            {
                "entity": "Annotation",
                "summary": "CVAT labeled · 1 bbox(es) · CAI-KOBO-790199718-topdown",
                "detail": "Auto-imported via CVAT job→completed (export_format=COCO).",
                "how": "Pulled via CVAT REST API after webhook/poller",
                "how_detail": (
                    "Task COCO export downloaded and normalized into Annotation.payload (1 label(s))."
                ),
            },
            {
                "entity": "Webhook",
                "summary": "CVAT job completed → auto-pull CVAT-CAI-KOBO-790199718-topdown (labels=1)",
                "detail": (
                    "Poller detected CVAT job state=completed (Docker Desktop blocks CVAT "
                    "webhook egress to private IPs — same pull path as a real webhook). "
                    "COCO export + normalize. Save alone does not trigger pull."
                ),
                "how": "Fetched via CVAT REST API (poller fallback)",
                "how_detail": "Poller saw job state=completed, then COCO export + normalize.",
            },
        ],
        "data": {
            "action": "auto_pull",
            "platform": "cvat",
            "event": "job completed",
            "source": "poller",
            "task_ref": "CVAT-CAI-KOBO-790199718-topdown",
            "pull": {
                "total_labels": 1,
                "created": 1,
                "images": [
                    {
                        "citizen_ai_image_id": "CAI-KOBO-790199718-topdown",
                        "labels_count": 1,
                    }
                ],
            },
        },
    },
]


REVIEW_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Activity log copy review</title>
  <style>
    :root {
      --bg: #0f172a; --panel: #111827; --border: #334155; --text: #e2e8f0;
      --muted: #94a3b8; --accent: #38bdf8; --ok: #22c55e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 20px 24px 48px; background: var(--bg); color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
    }
    h1 { margin: 0 0 6px; font-size: 1.1rem; }
    .sub { color: var(--muted); font-size: 0.8rem; margin-bottom: 18px; }
    .head {
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
      position: sticky; top: 0; z-index: 2; background: var(--bg); padding: 8px 0 12px;
      border-bottom: 1px solid var(--border); margin-bottom: 12px;
    }
    .head h2 {
      margin: 0; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
    }
    .pair {
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
      margin-bottom: 18px; padding-bottom: 18px; border-bottom: 1px solid var(--border);
    }
    .col {
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
    }
    .batch-label {
      color: var(--muted); font-size: 0.68rem; margin-bottom: 8px;
    }
    .activity-item {
      border-left: 3px solid var(--accent); padding: 6px 0 6px 10px; margin-bottom: 8px; font-size: 0.78rem;
    }
    .activity-item .when { color: var(--muted); font-size: 0.68rem; }
    .activity-item .what { margin-top: 2px; font-weight: 700; }
    .activity-item .detail { margin-top: 4px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
    .activity-item .how { margin-top: 8px; font-weight: 700; font-size: 0.78rem; }
    .activity-item .how-detail { margin-top: 4px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
  </style>
</head>
<body>
  <h1>Activity log — copy review</h1>
  <p class="sub">Non-operational. Left = original verbose text · Right = current concise text.</p>
  <div class="head">
    <h2>Original</h2>
    <h2>New</h2>
  </div>
  {% for case in cases %}
  <div class="pair">
    <div class="col">
      <div class="batch-label">{{ case.when }} · {{ case.action }} · {{ case.label }}</div>
      {% for item in case.before %}
      <div class="activity-item">
        <div class="when">{{ case.when }} · {{ case.action }}</div>
        <div class="what">{{ item.entity }} — {{ item.summary }}</div>
        {% if item.detail %}<div class="detail">{{ item.detail }}</div>{% endif %}
        {% if item.how %}<div class="how">{{ item.how }}</div>{% endif %}
        {% if item.how_detail %}<div class="how-detail">{{ item.how_detail }}</div>{% endif %}
      </div>
      {% endfor %}
    </div>
    <div class="col">
      <div class="batch-label">{{ case.when }} · {{ case.action }} · {{ case.label }}</div>
      {% for item in case.after %}
      <div class="activity-item">
        <div class="when">{{ case.when }} · {{ case.action }}</div>
        <div class="what">{{ item.entity }} — {{ item.summary }}</div>
        {% if item.detail %}<div class="detail">{{ item.detail }}</div>{% endif %}
        {% if item.how %}<div class="how">{{ item.how }}</div>{% endif %}
        {% if item.how_detail %}<div class="how-detail">{{ item.how_detail }}</div>{% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</body>
</html>
"""


def build_review_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for raw in _REVIEW_CASES:
        # Demo UI reverses activity items; match that order for side-by-side reading.
        after = list(reversed(entity_changes_for_action(raw["action"], raw.get("data") or {})))
        cases.append(
            {
                "label": raw["label"],
                "when": raw["when"],
                "action": raw["action"],
                "before": raw["before"],
                "after": after,
            }
        )
    return cases
