#!/usr/bin/env python3
"""Local demo UI for Orchestrator ↔ CVAT POC (screen recording friendly)."""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

from poc_service import (
    DEMO_IMAGE_ID,
    DEMO_IMAGE_PATH,
    DEMO_METADATA,
    DEMO_TASK_REF,
    DEFAULT_WEBHOOK_TARGET,
    PocError,
    PocSettings,
    active_demo_task_ref,
    demo_state,
    entity_changes_for_action,
    get_entities,
    get_status,
    get_webhook_feed,
    handle_cvat_webhook,
    init_poc,
    list_cvat_webhooks,
    next_demo_task_ref,
    poll_completed_jobs,
    pull_from_cvat,
    push_to_cvat,
    register_cvat_webhook,
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
      --entities-w: 360px;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
    header {
      padding: 14px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
    }
    header h1 { margin: 0; font-size: 1.1rem; }
    header p { margin: 4px 0 0; color: var(--muted); font-size: 0.82rem; }
    .layout {
      display: grid;
      grid-template-columns: 1fr var(--entities-w);
      min-height: calc(100vh - 72px);
    }
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
    .activity-item.latest-batch { border-left-color: var(--ok); }
    .activity-item .when { color: var(--muted); font-size: 0.68rem; }
    .activity-item .what { margin-top: 2px; font-weight: 600; }
    .activity-item .detail { margin-top: 6px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
    .empty { color: var(--muted); font-size: 0.78rem; font-style: italic; }
    .entity-count { color: var(--muted); font-weight: 400; font-size: 0.8rem; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
    }
    .summary-box {
      background: #0b1020;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      min-height: 110px;
    }
    .summary-box h3 {
      margin: 0 0 8px;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }
    .summary-box .title { font-weight: 700; font-size: 0.85rem; word-break: break-all; }
    .summary-box .body { margin-top: 6px; color: var(--muted); font-size: 0.74rem; line-height: 1.4; }
    .summary-box .when { margin-top: 8px; color: var(--muted); font-size: 0.68rem; }
    .summary-box .state { color: #86efac; font-weight: 600; }
    .state-flow {
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: 0;
      margin: 10px 0 4px;
    }
    .state-flow .flow-arrow {
      text-align: center;
      color: #94a3b8;
      font-size: 0.7rem;
      line-height: 1.1;
      padding: 2px 0;
      user-select: none;
    }
    .state-flow .flow-step {
      text-align: center;
      font-size: 0.78rem;
      font-weight: 600;
      padding: 7px 10px;
      border-radius: 8px;
      border: 1.5px solid #64748b;
      color: #e2e8f0;
      background: transparent;
      transition: border-color 0.25s, color 0.25s, background 0.25s, box-shadow 0.25s;
    }
    .state-flow .flow-step.upcoming {
      border-color: #64748b;
      color: #cbd5e1;
      opacity: 0.85;
    }
    .state-flow .flow-step.done {
      border-color: #166534;
      color: #86efac;
      background: rgba(22, 101, 52, 0.15);
    }
    .state-flow .flow-step.current {
      border-color: #22c55e;
      color: #86efac;
      background: rgba(34, 197, 94, 0.12);
      box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.25);
    }
    @media (max-width: 1100px) {
      .summary-grid { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 640px) {
      .summary-grid { grid-template-columns: 1fr; }
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Orchestrator ↔ CVAT — API Demo</h1>
    <p>Local POC · REST API · CVAT window beside this for recording</p>
  </header>

  <div class="layout" id="layout">
    <section class="center">
      <div class="card">
        <h2>Demo defaults</h2>
        <dl class="meta">
          <dt>Image ID</dt><dd id="demo-image-id"></dd>
          <dt>Task ref</dt><dd id="demo-task-ref"></dd>
          <dt>Webhook</dt><dd id="webhook-status">—</dd>
          <dt>CVAT link</dt><dd id="handoff-link">—</dd>
          <dt>Labels</dt><dd><span class="badge" id="labels-badge">—</span></dd>
        </dl>
      </div>
      <div class="card">
        <h2>Entity summaries</h2>
        <div class="summary-grid">
          <div class="summary-box" id="summary-image">
            <h3>Image</h3>
            <div class="empty">—</div>
          </div>
          <div class="summary-box" id="summary-task">
            <h3>Task</h3>
            <div class="empty">—</div>
          </div>
          <div class="summary-box" id="summary-task-image">
            <h3>TaskImage</h3>
            <div class="empty">—</div>
          </div>
          <div class="summary-box" id="summary-annotation">
            <h3>Annotation</h3>
            <div class="empty">—</div>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>Actions</h2>
        <div class="actions">
          <button type="button" data-action="init">1 · Initialize</button>
          <button type="button" data-action="seed">2 · Register image</button>
          <button type="button" data-action="push">3 · Push to CVAT</button>
          <a class="link-btn secondary" id="btn-open-cvat" href="#" target="_blank" rel="noopener">4 · Open CVAT ↗</a>
          <button type="button" class="secondary" data-action="status">5 · Refresh</button>
          <button type="button" class="danger secondary" id="btn-reset">Reset all</button>
        </div>
        <p class="note">
          After annotate: Menu → Change job state → <strong>completed</strong>.
          Orchestrator auto-pulls (poller every few seconds; webhook endpoint also ready for production).
          On Docker Desktop for Mac, CVAT webhooks to private IPs are blocked — the poller is the sandbox workaround.
          Save alone does not pull — only job completed.
          <strong>Refresh</strong> reloads local entities + live CVAT status (read-only; does not import annotations).
        </p>
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
    let state = {};
    let entities = {};
    let activityLog = [];
    let highlightKeys = new Set();
    let busy = false;
    let busyTimer = null;
    let lastWebhookFeedId = 0;
    const seenWebhookEventIds = new Set();
    let feedPollInFlight = false;

    function setBusy(on) {
      busy = on;
      if (busyTimer) { clearTimeout(busyTimer); busyTimer = null; }
      document.querySelectorAll('button').forEach(b => {
        if (on) b.classList.add('busy'); else b.classList.remove('busy');
        b.disabled = !!on;
      });
      if (on) {
        busyTimer = setTimeout(() => {
          busy = false;
          document.querySelectorAll('button').forEach(b => {
            b.classList.remove('busy');
            b.disabled = false;
          });
          const out = document.getElementById('output');
          out.textContent = (out.textContent || '') + "\\n[timeout] Buttons re-enabled.";
          out.style.color = '#fca5a5';
        }, 30000);
      }
    }

    document.querySelectorAll('button[data-action]').forEach(btn => {
      btn.addEventListener('click', () => run(btn.getAttribute('data-action')));
    });
    document.getElementById('btn-reset').addEventListener('click', () => runReset());

    function shortTime(isoOrLocal) {
      if (!isoOrLocal) return '';
      try {
        const d = new Date(isoOrLocal.includes('T') || isoOrLocal.includes('Z')
          ? (isoOrLocal.endsWith('Z') || isoOrLocal.includes('+') ? isoOrLocal : isoOrLocal + 'Z')
          : isoOrLocal);
        if (!Number.isNaN(d.getTime())) return d.toLocaleTimeString();
      } catch (_) {}
      return String(isoOrLocal).slice(0, 19);
    }

    function setSummary(id, html) {
      const el = document.getElementById(id);
      const title = el.querySelector('h3');
      el.innerHTML = '';
      el.appendChild(title);
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      while (wrap.firstChild) el.appendChild(wrap.firstChild);
    }

    function stateFlowHtml(steps, currentKey) {
      const idx = steps.findIndex(s => s.key === currentKey);
      const active = idx < 0 ? 0 : idx;
      return `<div class="state-flow">${steps.map((s, i) => {
        let cls = 'upcoming';
        if (i < active) cls = 'done';
        else if (i === active) cls = 'current';
        const arrow = i < steps.length - 1 ? '<div class="flow-arrow">↓</div>' : '';
        return `<div class="flow-step ${cls}">${s.label}</div>${arrow}`;
      }).join('')}</div>`;
    }

    const IMAGE_FLOW = [
      { key: 'validated', label: 'validated' },
      { key: 'queued_for_annotation', label: 'queued' },
      { key: 'annotated', label: 'annotated' },
    ];
    const TASK_FLOW = [
      { key: 'created', label: 'created' },
      { key: 'annotated', label: 'annotated' },
    ];

    function renderSummaries() {
      const e = entities;
      const imgs = e.images || [];
      const tasks = e.tasks || [];
      const links = e.task_images || [];
      const anns = e.annotations || [];

      if (!imgs.length) {
        setSummary('summary-image', '<div class="empty">—</div>');
      } else {
        const r = imgs[imgs.length - 1];
        let body = 'This simulates one image that has been ingested from Kobo, processed and stored.';
        if (r.workflow_state === 'queued_for_annotation') {
          body = 'Queued for annotation in CVAT after push. Metadata stays in Orchestrator only.';
        } else if (r.workflow_state === 'annotated') {
          body = 'Marked annotated after job completed → auto-pull imported labels.';
        }
        setSummary('summary-image', `
          <div class="title">${r.citizen_ai_image_id}</div>
          ${stateFlowHtml(IMAGE_FLOW, r.workflow_state || 'validated')}
          <div class="body">${body}</div>
          <div class="when">${shortTime(r.created_at)}</div>
        `);
      }

      if (!tasks.length) {
        setSummary('summary-task', '<div class="empty">—</div>');
      } else {
        const r = tasks[tasks.length - 1];
        let body = `CVAT task #${r.external_task_id || '—'} · created for annotation handoff.`;
        if (r.status === 'annotated') {
          body = `CVAT task #${r.external_task_id || '—'} · status annotated after auto-pull.`;
        }
        setSummary('summary-task', `
          <div class="title">${r.task_ref}</div>
          ${stateFlowHtml(TASK_FLOW, r.status || 'created')}
          <div class="body">${body}</div>
          <div class="when">${shortTime(r.created_at)}</div>
        `);
      }

      if (!links.length) {
        setSummary('summary-task-image', '<div class="empty">—</div>');
      } else {
        const r = links[links.length - 1];
        setSummary('summary-task-image', `
          <div class="title">${r.task_ref} ↔ ${r.citizen_ai_image_id}</div>
          <div class="body">Junction row linking the Orchestrator task to this image. No status column — unchanged after pull.</div>
          <div class="when">${shortTime(r.created_at) || 'linked on push'}</div>
        `);
      }

      if (!anns.length) {
        const waiting = tasks.length
          ? '<div class="empty">Not yet — waiting for CVAT job completed</div>'
          : '<div class="empty">—</div>';
        setSummary('summary-annotation', waiting);
      } else {
        const r = anns[anns.length - 1];
        setSummary('summary-annotation', `
          <div class="title">${r.citizen_ai_image_id} <span class="state">${r.labels_count ?? '?'} labels</span></div>
          <div class="body">Created on auto-pull after job completed (${r.export_format || 'COCO'}).</div>
          <div class="when">${shortTime(r.created_at)}</div>
        `);
      }
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
      const items = activityLog.slice().reverse();
      const latestWhen = items[0]?.when;
      let inLatestBatch = true;
      el.innerHTML = items.map(item => {
        if (item.when !== latestWhen) inLatestBatch = false;
        const latest = inLatestBatch ? ' latest-batch' : '';
        return `
        <div class="activity-item${latest}">
          <div class="when">${item.when} · ${item.action}</div>
          <div class="what">${item.entity} — ${item.summary}</div>
          ${item.detail ? `<div class="detail">${item.detail}</div>` : ''}
        </div>
      `;
      }).join('');
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
      const wh = state.webhook;
      const whEl = document.getElementById('webhook-status');
      if (wh?.is_active) {
        whEl.innerHTML = `<span class="badge ok">on · #${wh.id}</span> <span style="color:var(--muted);font-size:0.75rem">job→completed · always on</span>`;
      } else {
        whEl.innerHTML = '<span class="badge warn">registering…</span> <span style="color:var(--muted);font-size:0.75rem">auto on init / startup</span>';
      }
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
      renderEntities();
      renderSummaries();
      renderActivity();
      if (payload.changes) applyHighlightKeys(payload.changes);
    }

    async function refreshState() {
      const res = await fetch('/api/state');
      return res.json();
    }

    async function run(action) {
      if (busy) {
        document.getElementById('output').textContent = 'Still running previous action…';
        return;
      }
      setBusy(true);
      const out = document.getElementById('output');
      out.style.color = '';
      out.textContent = 'Running ' + action + '...';
      try {
        const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' };
        const res = await fetch('/api/' + action, action === 'status' ? { method: 'GET' } : opts);
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Request failed');
        out.textContent = JSON.stringify(data.data, null, 2);
        updateUi({ ...data, lastAction: action });
      } catch (err) {
        out.textContent = String(err);
        out.style.color = '#fca5a5';
      } finally {
        setBusy(false);
      }
    }

    async function runReset() {
      if (busy) {
        document.getElementById('output').textContent = 'Still running previous action…';
        return;
      }
      setBusy(true);
      const out = document.getElementById('output');
      out.style.color = '';
      out.textContent = 'Resetting Orchestrator DB + deleting CVAT project tasks…';
      try {
        const res = await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Reset failed');
        activityLog = [];
        seenWebhookEventIds.clear();
        lastWebhookFeedId = 0;
        document.getElementById('labels-big').textContent = '';
        document.getElementById('labels-badge').textContent = '—';
        document.getElementById('labels-badge').className = 'badge';
        document.getElementById('handoff-link').textContent = '—';
        document.getElementById('btn-open-cvat').href = '#';
        out.textContent = JSON.stringify(data.data, null, 2);
        updateUi({ ...data, lastAction: 'reset' });
      } catch (err) {
        out.textContent = String(err);
        out.style.color = '#fca5a5';
      } finally {
        setBusy(false);
      }
    }

    async function pollWebhookFeed() {
      if (feedPollInFlight) return;
      feedPollInFlight = true;
      try {
        const res = await fetch('/api/webhooks/feed?after_id=' + lastWebhookFeedId);
        const data = await res.json();
        if (!data.ok) return;
        const events = data.events || [];
        if (!events.length) return;
        for (const ev of events) {
          const eid = Number(ev.id) || 0;
          if (!eid || seenWebhookEventIds.has(eid)) {
            lastWebhookFeedId = Math.max(lastWebhookFeedId, eid);
            continue;
          }
          seenWebhookEventIds.add(eid);
          lastWebhookFeedId = Math.max(lastWebhookFeedId, eid);
          const changes = data.changes_by_id?.[String(eid)] || data.changes_by_id?.[eid] || [{
            entity: 'Webhook',
            summary: `${ev.event} → ${ev.action}`,
            detail: ev.reason || JSON.stringify(ev.pull || {}),
          }];
          updateUi({
            state: data.state,
            entities: data.entities,
            changes,
            lastAction: 'webhook',
          });
          if (ev.action === 'auto_pull') {
            document.getElementById('output').textContent = JSON.stringify(ev, null, 2);
            document.getElementById('output').style.color = '';
          }
        }
      } catch (_) { /* ignore poll errors */ }
      finally {
        feedPollInFlight = false;
      }
    }

    window.run = run;
    window.runReset = runReset;
    refreshState().then(payload => {
      updateUi(payload);
      const feed = payload.state?.webhook_feed || [];
      for (const e of feed) {
        const eid = Number(e.id) || 0;
        if (eid) {
          seenWebhookEventIds.add(eid);
          lastWebhookFeedId = Math.max(lastWebhookFeedId, eid);
        }
      }
    }).catch(err => {
      document.getElementById('output').textContent = 'Failed to load state: ' + err;
      document.getElementById('output').style.color = '#fca5a5';
    });
    setInterval(pollWebhookFeed, 2000);
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
        try:
            wh = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
            data["webhook"] = wh
        except Exception as wh_exc:  # noqa: BLE001
            data["webhook"] = {"error": str(wh_exc), "is_active": False}
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
            task_ref=next_demo_task_ref(SETTINGS),
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
            task_ref=active_demo_task_ref(SETTINGS),
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


@app.post("/api/webhooks/register")
def api_webhooks_register():
    try:
        data = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("register_webhook", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.get("/api/webhooks/feed")
def api_webhooks_feed():
    try:
        after_id = int(request.args.get("after_id") or 0)
        events = get_webhook_feed(after_id=after_id)
        state = demo_state(SETTINGS)
        changes_by_id = {
            str(ev["id"]): entity_changes_for_action("webhook", ev) for ev in events
        }
        return jsonify(
            {
                "ok": True,
                "events": events,
                "changes_by_id": changes_by_id,
                "state": {**state, "completed": _completed_flags(state), "next": _next_step(_completed_flags(state))},
                "entities": get_entities(SETTINGS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/webhooks/cvat")
def api_webhooks_cvat():
    """CVAT → Orchestrator webhook receiver (job completed → auto-pull)."""
    try:
        payload = request.get_json(silent=True) or {}
        result = handle_cvat_webhook(payload, settings=SETTINGS, force_pull=True)
        # Always 200 so CVAT does not retry forever on ignored events.
        return jsonify({"ok": True, "result": result}), 200
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/webhooks")
def api_webhooks_list():
    try:
        data = list_cvat_webhooks(SETTINGS)
        return jsonify({"ok": True, "data": data})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.get("/api/status")
def api_status():
    try:
        data = get_status(task_ref=active_demo_task_ref(SETTINGS), settings=SETTINGS)
        state = demo_state(SETTINGS)
        return jsonify(_full_payload("status", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    init_poc(SETTINGS)
    print("Demo UI: http://127.0.0.1:5050")
    print(f"Next demo task ref: {next_demo_task_ref(SETTINGS)}")
    print(f"Webhook target (from CVAT Docker): {DEFAULT_WEBHOOK_TARGET}")
    try:
        wh = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
        print(f"Webhook always-on: #{wh.get('webhook_id')} → {wh.get('target_url')}")
    except Exception as exc:  # noqa: BLE001
        print(f"Webhook auto-register skipped: {exc}")

    def _poll_loop() -> None:
        # Wait for app listen; then poll CVAT job completion (Mac webhook workaround).
        time.sleep(3)
        while True:
            try:
                poll_completed_jobs(settings=SETTINGS, force_pull=False)
            except Exception as exc:  # noqa: BLE001
                print(f"[poller] {exc}")
            time.sleep(4)

    threading.Thread(target=_poll_loop, name="cvat-complete-poller", daemon=True).start()
    # 0.0.0.0 so other containers / host mapping can reach us
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
