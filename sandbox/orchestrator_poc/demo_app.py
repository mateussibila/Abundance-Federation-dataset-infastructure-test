#!/usr/bin/env python3
"""Local demo UI for Orchestrator ↔ CVAT POC (screen recording friendly)."""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

from poc_service import (
    DEMO_IMAGE_ID,
    DEMO_IMAGE_PATH,
    DEMO_METADATA,
    DEMO_TASK_REF,
    PocError,
    PocSettings,
    demo_state,
    entity_changes_for_action,
    get_entities,
    get_status,
    init_poc,
    pull_from_cvat,
    push_to_cvat,
    reset_all,
    seed_image,
)

app = Flask(__name__)
SETTINGS = PocSettings()

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Orchestrator ↔ CVAT Demo</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --border: #2d3a4d;
      --text: #e6edf3;
      --muted: #8b9cb3;
      --accent: #3b82f6;
      --ok: #22c55e;
      --warn: #f59e0b;
      --err: #ef4444;
      --sidebar-w: 280px;
      --entities-w: 360px;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
    header {
      padding: 14px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
      display: flex; align-items: center; gap: 14px;
    }
    header .title-block { flex: 1; min-width: 0; }
    header h1 { margin: 0; font-size: 1.1rem; }
    header p { margin: 4px 0 0; color: var(--muted); font-size: 0.82rem; }
    .header-actions { display: flex; gap: 8px; flex-shrink: 0; align-items: center; }
    .layout {
      display: grid;
      grid-template-columns: var(--sidebar-w) 1fr var(--entities-w);
      min-height: calc(100vh - 72px);
      transition: grid-template-columns 0.2s ease;
    }
    .layout.sidebar-collapsed { grid-template-columns: 52px 1fr var(--entities-w); }
    .sidebar {
      border-right: 1px solid var(--border); background: #121820;
      overflow: hidden; transition: width 0.2s ease;
    }
    .sidebar-inner { padding: 16px; width: var(--sidebar-w); }
    .layout.sidebar-collapsed .sidebar-inner { padding: 12px 8px; width: 52px; }
    .layout.sidebar-collapsed .step-text { display: none; }
    .layout.sidebar-collapsed .sidebar-title { display: none; }
    .sidebar-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 12px; }
    .step { display: flex; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); align-items: flex-start; }
    .step-num {
      width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
      font-size: 0.75rem; font-weight: 700; background: var(--border); flex-shrink: 0;
    }
    .step.done .step-num { background: var(--ok); color: #052e16; }
    .step.active .step-num { background: var(--accent); color: white; }
    .step-title { font-weight: 600; font-size: 0.88rem; }
    .step-desc { color: var(--muted); font-size: 0.76rem; margin-top: 2px; line-height: 1.3; }
    .center { padding: 20px; overflow: auto; min-width: 0; }
    .entities {
      border-left: 1px solid var(--border); background: #101722;
      overflow: auto; padding: 16px;
    }
    .entities h2 { margin: 0 0 10px; font-size: 0.95rem; }
    .entities h3 {
      margin: 16px 0 8px; font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: 0.05em; color: var(--muted);
    }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
    .card h2 { margin: 0 0 10px; font-size: 0.95rem; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
    button, .link-btn {
      appearance: none; border: none; border-radius: 8px; padding: 9px 14px;
      font-size: 0.85rem; font-weight: 600; cursor: pointer; background: var(--accent); color: white;
      text-decoration: none; display: inline-block;
    }
    button.icon { padding: 8px 10px; min-width: 36px; background: #334155; }
    button.danger { background: #991b1b; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    button.busy { opacity: 0.7; }
    .meta { display: grid; grid-template-columns: 120px 1fr; gap: 4px 10px; font-size: 0.82rem; }
    .meta dt { color: var(--muted); }
    .meta dd { margin: 0; word-break: break-all; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; background: #334155; }
    .badge.ok { background: #14532d; color: #86efac; }
    .badge.warn { background: #78350f; color: #fcd34d; }
    .output {
      background: #0b1020; border: 1px solid var(--border); border-radius: 8px; padding: 12px;
      font-family: ui-monospace, Menlo, monospace; font-size: 0.74rem; white-space: pre-wrap;
      word-break: break-word; max-height: 220px; overflow: auto;
    }
    .labels-big { font-size: 1.6rem; font-weight: 800; color: var(--ok); margin: 6px 0; }
    label.force { display: flex; align-items: center; gap: 8px; font-size: 0.82rem; color: var(--muted); }
    .note { color: var(--muted); font-size: 0.8rem; margin-top: 8px; line-height: 1.4; }
    .entity-row {
      background: #1a2332; border: 1px solid var(--border); border-radius: 8px;
      padding: 10px; margin-bottom: 8px; font-size: 0.78rem; line-height: 1.45;
      transition: border-color 0.3s, box-shadow 0.3s;
    }
    .entity-row.highlight {
      border-color: var(--ok); box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.35);
      animation: pulse 1.2s ease;
    }
    @keyframes pulse {
      0%, 100% { background: #1a2332; }
      50% { background: #1f2d24; }
    }
    .entity-row .key { color: var(--muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .entity-row .val { word-break: break-all; }
    .activity-item {
      border-left: 3px solid var(--accent); padding: 6px 0 6px 10px; margin-bottom: 8px; font-size: 0.78rem;
    }
    .activity-item .when { color: var(--muted); font-size: 0.68rem; }
    .activity-item .what { margin-top: 2px; font-weight: 600; }
    .activity-item .detail { margin-top: 6px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
    .empty { color: var(--muted); font-size: 0.78rem; font-style: italic; }
    .entity-count { color: var(--muted); font-weight: 400; font-size: 0.8rem; }
  </style>
</head>
<body>
  <header>
    <div class="header-actions">
      <button class="icon secondary" id="toggle-sidebar" title="Toggle steps panel">☰</button>
    </div>
    <div class="title-block">
      <h1>Orchestrator ↔ CVAT — API Demo</h1>
      <p>Local POC · REST API · CVAT window beside this for recording</p>
    </div>
  </header>

  <div class="layout" id="layout">
    <aside class="sidebar">
      <div class="sidebar-inner">
        <div class="sidebar-title">Workflow steps</div>
        <div id="steps"></div>
      </div>
    </aside>

    <section class="center">
      <div class="card">
        <h2>Demo defaults</h2>
        <dl class="meta">
          <dt>Image ID</dt><dd id="demo-image-id"></dd>
          <dt>Task ref</dt><dd id="demo-task-ref"></dd>
          <dt>CVAT link</dt><dd id="handoff-link">—</dd>
          <dt>Labels</dt><dd><span class="badge" id="labels-badge">—</span></dd>
        </dl>
      </div>
      <div class="card">
        <h2>Actions</h2>
        <div class="actions">
          <button onclick="run('init')">1 · Initialize</button>
          <button onclick="run('seed')">2 · Register image</button>
          <button onclick="run('push')">3 · Push to CVAT</button>
          <a class="link-btn secondary" id="btn-open-cvat" href="#" target="_blank" rel="noopener">4 · Open CVAT ↗</a>
          <button onclick="run('pull')">5 · Pull annotations</button>
          <button class="secondary" onclick="run('status')">6 · Refresh</button>
          <button class="danger secondary" onclick="runReset()">Reset all</button>
        </div>
        <label class="force"><input type="checkbox" id="force-pull" /> Force re-import on pull</label>
        <p class="note">Step 4: annotate in CVAT (rectangle + Save). Step 5 imports COCO back.</p>
      </div>
      <div class="card">
        <h2>Last action result</h2>
        <div class="labels-big" id="labels-big"></div>
        <div class="output" id="output">Ready.</div>
      </div>
    </section>

    <aside class="entities">
      <h2>Entities <span class="entity-count" id="entity-counts"></span></h2>
      <h3>Activity log</h3>
      <div id="activity-log"><div class="empty">Run an action to see records created.</div></div>
      <h3>Images</h3>
      <div id="entity-images"><div class="empty">No images yet.</div></div>
      <h3>Tasks</h3>
      <div id="entity-tasks"><div class="empty">No tasks yet.</div></div>
      <h3>Task ↔ Image links</h3>
      <div id="entity-task-images"><div class="empty">No links yet.</div></div>
      <h3>Annotations</h3>
      <div id="entity-annotations"><div class="empty">No annotations yet.</div></div>
    </aside>
  </div>

  <script>
    const STEPS = [
      { id: 'init', title: 'Initialize', desc: 'Create local SQLite (POC only)' },
      { id: 'seed', title: 'Register image', desc: 'Store image in Orchestrator' },
      { id: 'push', title: 'Push to CVAT', desc: 'API: create task + upload' },
      { id: 'cvat', title: 'Annotate in CVAT', desc: 'Manual step in browser' },
      { id: 'pull', title: 'Pull annotations', desc: 'API: export COCO + normalize' },
      { id: 'status', title: 'Status', desc: 'Verify labels imported' },
    ];

    let state = {};
    let entities = {};
    let activityLog = [];
    let highlightKeys = new Set();
    let busy = false;

    document.getElementById('toggle-sidebar').addEventListener('click', () => {
      document.getElementById('layout').classList.toggle('sidebar-collapsed');
    });

    function renderSteps() {
      const done = state.completed || {};
      const active = state.next || 'init';
      document.getElementById('steps').innerHTML = STEPS.map((s, i) => {
        const cls = done[s.id] ? 'done' : (s.id === active ? 'active' : '');
        return `<div class="step ${cls}"><div class="step-num">${i+1}</div><div class="step-text"><div class="step-title">${s.title}</div><div class="step-desc">${s.desc}</div></div></div>`;
      }).join('');
    }

    function entityKey(table, row) {
      if (table === 'task_images') return `ti:${row.task_id}:${row.image_id}`;
      return `${table}:${row.id || row.task_ref || row.citizen_ai_image_id}`;
    }

    function rowHtml(table, row, fields) {
      const key = entityKey(table, row);
      const hl = highlightKeys.has(key) ? ' highlight' : '';
      const lines = fields.map(([label, val]) =>
        `<div><span class="key">${label}</span><div class="val">${val ?? '—'}</div></div>`
      ).join('');
      return `<div class="entity-row${hl}" data-key="${key}">${lines}</div>`;
    }

    function renderEntities() {
      const e = entities;
      const counts = `${(e.images||[]).length} img · ${(e.tasks||[]).length} task · ${(e.task_images||[]).length} link · ${(e.annotations||[]).length} ann`;
      document.getElementById('entity-counts').textContent = `(${counts})`;

      const imgEl = document.getElementById('entity-images');
      imgEl.innerHTML = (e.images||[]).length
        ? e.images.map(r => rowHtml('images', r, [
            ['Image', r.citizen_ai_image_id],
            ['workflow_state', r.workflow_state],
            ['created_at', r.created_at],
          ])).join('')
        : '<div class="empty">No images yet.</div>';

      const taskEl = document.getElementById('entity-tasks');
      taskEl.innerHTML = (e.tasks||[]).length
        ? e.tasks.map(r => rowHtml('tasks', r, [
            ['Task', r.task_ref],
            ['external_task_id (CVAT)', r.external_task_id],
            ['status', r.status],
            ['handoff_url', r.handoff_url],
          ])).join('')
        : '<div class="empty">No tasks yet.</div>';

      const tiEl = document.getElementById('entity-task-images');
      tiEl.innerHTML = (e.task_images||[]).length
        ? e.task_images.map(r => rowHtml('task_images', r, [
            ['Link', `${r.task_ref} ↔ ${r.citizen_ai_image_id}`],
          ])).join('')
        : '<div class="empty">No links yet.</div>';

      const annEl = document.getElementById('entity-annotations');
      annEl.innerHTML = (e.annotations||[]).length
        ? e.annotations.map(r => rowHtml('annotations', r, [
            ['Annotation', r.citizen_ai_image_id],
            ['labels', r.labels_count ?? '?'],
            ['export_format', r.export_format],
            ['created_at', r.created_at],
          ])).join('')
        : '<div class="empty">No annotations yet.</div>';
    }

    function renderActivity() {
      const el = document.getElementById('activity-log');
      if (!activityLog.length) {
        el.innerHTML = '<div class="empty">Run an action to see records created.</div>';
        return;
      }
      el.innerHTML = activityLog.slice().reverse().map(item => `
        <div class="activity-item">
          <div class="when">${item.when} · ${item.action}</div>
          <div class="what">${item.entity} — ${item.summary}</div>
          ${item.detail ? `<div class="detail">${item.detail}</div>` : ''}
        </div>
      `).join('');
    }

    function applyHighlightKeys(changes) {
      highlightKeys.clear();
      const e = entities;
      for (const ch of changes || []) {
        if (ch.entity === 'Image' && e.images?.length) {
          e.images.forEach(r => highlightKeys.add(entityKey('images', r)));
        }
        if (ch.entity === 'Task' && e.tasks?.length) {
          highlightKeys.add(entityKey('tasks', e.tasks[e.tasks.length - 1]));
        }
        if (ch.entity === 'TaskImage' && e.task_images?.length) {
          highlightKeys.add(entityKey('task_images', e.task_images[e.task_images.length - 1]));
        }
        if (ch.entity === 'Annotation' && !ch.summary.startsWith('Skipped') && e.annotations?.length) {
          highlightKeys.add(entityKey('annotations', e.annotations[e.annotations.length - 1]));
        }
      }
      setTimeout(() => { highlightKeys.clear(); renderEntities(); }, 2500);
    }

    function updateUi(payload) {
      state = payload.state || state;
      entities = payload.entities || entities;
      if (payload.changes?.length) {
        const when = new Date().toLocaleTimeString();
        payload.changes.forEach(c => activityLog.push({ when, action: payload.lastAction || 'action', ...c }));
      }
      document.getElementById('demo-image-id').textContent = state.demo?.image_id || '—';
      document.getElementById('demo-task-ref').textContent = state.demo?.task_ref || '—';
      const task = state.task?.task;
      const handoff = task?.handoff_url;
      const linkEl = document.getElementById('handoff-link');
      const openBtn = document.getElementById('btn-open-cvat');
      if (handoff) {
        linkEl.innerHTML = `<a href="${handoff}" target="_blank" rel="noopener">${handoff}</a>`;
        openBtn.href = handoff; openBtn.style.pointerEvents = 'auto'; openBtn.style.opacity = '1';
      } else {
        linkEl.textContent = '— (push first)';
        openBtn.href = '#'; openBtn.style.pointerEvents = 'none'; openBtn.style.opacity = '0.45';
      }
      const labels = state.task?.annotations?.[0]?.labels_count;
      const badge = document.getElementById('labels-badge');
      const big = document.getElementById('labels-big');
      if (labels !== undefined && labels !== null) {
        badge.textContent = String(labels);
        badge.className = 'badge ' + (labels > 0 ? 'ok' : 'warn');
        big.textContent = labels > 0 ? `labels = ${labels}` : '';
      }
      renderSteps();
      renderEntities();
      renderActivity();
      if (payload.changes) applyHighlightKeys(payload.changes);
    }

    async function refreshState() {
      const res = await fetch('/api/state');
      return res.json();
    }

    async function run(action) {
      if (busy) return;
      busy = true;
      document.querySelectorAll('button').forEach(b => b.classList.add('busy'));
      document.getElementById('output').textContent = 'Running ' + action + '...';
      try {
        const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' };
        if (action === 'pull') opts.body = JSON.stringify({ force: document.getElementById('force-pull').checked });
        const res = await fetch('/api/' + action, action === 'status' ? { method: 'GET' } : opts);
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Request failed');
        document.getElementById('output').textContent = JSON.stringify(data.data, null, 2);
        updateUi({ ...data, lastAction: action });
      } catch (err) {
        document.getElementById('output').textContent = String(err);
        document.getElementById('output').style.color = '#fca5a5';
      } finally {
        busy = false;
        document.querySelectorAll('button').forEach(b => b.classList.remove('busy'));
      }
    }

    async function runReset() {
      if (busy) return;
      if (!confirm('Clear all Orchestrator records (images, tasks, annotations)? CVAT tasks are not deleted.')) return;
      busy = true;
      document.querySelectorAll('button').forEach(b => b.classList.add('busy'));
      try {
        const res = await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Reset failed');
        activityLog = [];
        highlightKeys.clear();
        document.getElementById('labels-big').textContent = '';
        document.getElementById('labels-badge').textContent = '—';
        document.getElementById('labels-badge').className = 'badge';
        document.getElementById('handoff-link').textContent = '—';
        document.getElementById('btn-open-cvat').href = '#';
        document.getElementById('output').textContent = JSON.stringify(data.data, null, 2);
        updateUi({ ...data, lastAction: 'reset' });
      } catch (err) {
        document.getElementById('output').textContent = String(err);
      } finally {
        busy = false;
        document.querySelectorAll('button').forEach(b => b.classList.remove('busy'));
      }
    }

    refreshState().then(updateUi);
  </script>
</body>
</html>
"""


def _completed_flags(state: dict) -> dict[str, bool]:
    task = state.get("task")
    annotations = (task or {}).get("annotations") or []
    labels = annotations[0]["labels_count"] if annotations else 0
    return {
        "init": state.get("db_exists", False),
        "seed": state.get("image_registered", False),
        "push": bool(task and task.get("task", {}).get("external_task_id")),
        "cvat": labels > 0,
        "pull": bool(annotations),
        "status": bool(task),
    }


def _next_step(completed: dict[str, bool]) -> str:
    order = ["init", "seed", "push", "cvat", "pull", "status"]
    for step in order:
        if step == "cvat":
            if completed.get("push") and not completed.get("pull"):
                return "cvat"
            continue
        if not completed.get(step):
            return step
    return "status"


def _full_payload(action: str, data: dict, state: dict) -> dict:
    completed = _completed_flags(state)
    return {
        "ok": True,
        "data": data,
        "state": {**state, "completed": completed, "next": _next_step(completed)},
        "entities": get_entities(SETTINGS),
        "changes": entity_changes_for_action(action, data),
    }


def _state_payload() -> dict:
    state = demo_state(SETTINGS)
    completed = _completed_flags(state)
    return {
        "ok": True,
        "state": {**state, "completed": completed, "next": _next_step(completed)},
        "entities": get_entities(SETTINGS),
        "changes": [],
    }


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/state")
def api_state():
    return jsonify(_state_payload())


@app.post("/api/init")
def api_init():
    try:
        data = init_poc(SETTINGS)
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("init", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/seed")
def api_seed():
    try:
        data = seed_image(
            image_id=DEMO_IMAGE_ID,
            path=DEMO_IMAGE_PATH,
            metadata=DEMO_METADATA,
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("seed", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/push")
def api_push():
    try:
        data = push_to_cvat(
            image_id=DEMO_IMAGE_ID,
            task_ref=DEMO_TASK_REF,
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("push", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/pull")
def api_pull():
    try:
        body = request.get_json(silent=True) or {}
        data = pull_from_cvat(
            task_ref=DEMO_TASK_REF,
            force=bool(body.get("force")),
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("pull", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/reset")
def api_reset():
    try:
        data = reset_all(SETTINGS)
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("reset", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.get("/api/status")
def api_status():
    try:
        data = get_status(task_ref=DEMO_TASK_REF, settings=SETTINGS)
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("status", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    init_poc(SETTINGS)
    print("Demo UI: http://127.0.0.1:5050")
    print(f"Demo task ref: {DEMO_TASK_REF}")
    app.run(host="127.0.0.1", port=5050, debug=False)
