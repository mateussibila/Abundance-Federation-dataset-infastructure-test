#!/usr/bin/env python3
"""Local demo UI for Orchestrator ↔ CVAT POC (screen recording friendly)."""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timezone

from activity_log_review import REVIEW_PAGE, build_review_cases
from flask import Flask, jsonify, render_template_string, request

from poc_service import (
    DEMO_IMAGE_ID,
    DEMO_IMAGE_PATH,
    DEMO_METADATA,
    DEMO_TASK_REF,
    DEFAULT_LS_WEBHOOK_TARGET,
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
    handle_ls_webhook,
    init_poc,
    list_cvat_webhooks,
    list_ls_webhooks,
    next_demo_task_ref,
    normalize_platform,
    poll_completed_jobs,
    poll_ls_completed_tasks,
    pull_from_kobo,
    pull_from_platform,
    push_latest_to_cvat,
    push_latest_to_ls,
    push_to_platform,
    register_cvat_webhook,
    register_ls_webhook,
    reset_all,
    seed_image,
)

app = Flask(__name__)
SETTINGS = PocSettings()


def _request_platform() -> str:
    body = request.get_json(silent=True) or {}
    q = request.args.get("platform")
    return normalize_platform(body.get("platform") or q or SETTINGS.platform)

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Orchestrator ↔ Annotation Demo</title>
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
    .activity-item .what { margin-top: 2px; font-weight: 700; }
    .activity-item .detail { margin-top: 4px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
    .activity-item .how { margin-top: 8px; font-weight: 700; font-size: 0.78rem; }
    .activity-item .how-detail { margin-top: 4px; color: var(--muted); font-size: 0.74rem; line-height: 1.45; font-weight: 400; }
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
    .plat-badge {
      display: inline-block;
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 2px 7px;
      border-radius: 4px;
      vertical-align: middle;
      margin-left: 6px;
    }
    .plat-badge.ls {
      background: rgba(37, 99, 235, 0.25);
      color: #93c5fd;
      border: 1px solid #3b82f6;
    }
    .plat-badge.cvat {
      background: rgba(5, 150, 105, 0.2);
      color: #6ee7b7;
      border: 1px solid #10b981;
    }
    .summary-box .ann-block {
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed #334155;
    }
    .summary-box .ann-block:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
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
    <h1>Orchestrator ↔ Annotation — API Demo</h1>
    <p>Local POC · REST API · CVAT (:8080) or Label Studio (:8081) beside this for recording</p>
  </header>

  <div class="layout" id="layout">
    <section class="center">
      <div class="card">
        <h2>Demo defaults</h2>
        <dl class="meta">
          <dt>Platform</dt>
          <dd>
            <select id="platform-select" style="background:#0b1020;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:4px 8px;">
              <option value="cvat">CVAT (:8080)</option>
              <option value="label_studio">Label Studio (:8081)</option>
            </select>
          </dd>
          <dt>Image ID</dt><dd id="demo-image-id"></dd>
          <dt>Task ref</dt><dd id="demo-task-ref"></dd>
          <dt>Webhook</dt><dd id="webhook-status">—</dd>
          <dt>Handoff</dt><dd id="handoff-link">—</dd>
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
          <button type="button" data-action="pull_kobo">2 · Pull from Kobo</button>
          <button type="button" data-action="seed" class="secondary">2b · Register dummy</button>
          <button type="button" data-action="push_ls">3 · Push to LS</button>
          <a class="link-btn secondary" id="btn-open-ls" href="#" target="_blank" rel="noopener">Open LS ↗</a>
          <button type="button" data-action="push_cvat">4 · Push to CVAT</button>
          <a class="link-btn secondary" id="btn-open-cvat" href="#" target="_blank" rel="noopener">Open CVAT ↗</a>
          <button type="button" class="secondary" data-action="status">Refresh</button>
          <button type="button" class="danger secondary" id="btn-reset">Reset all</button>
        </div>
        <p class="note" id="platform-note">
          <strong>Pull from Kobo</strong> = 1 submission → <code>images</code> only.
          Then <strong>Push to LS</strong> and/or <strong>Push to CVAT</strong> (latest image) — separate steps.
          Use <strong>Open LS</strong> / <strong>Open CVAT</strong> after a successful push.
          Platform selector below is only for Reset / webhook status.
          <strong>Reset All</strong> clears DB/media and restarts the Kobo cursor (deletes tasks on selected platform only).
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
    let selectedPlatform = localStorage.getItem('demo_platform') || 'cvat';

    function currentPlatform() {
      const el = document.getElementById('platform-select');
      return (el && el.value) || selectedPlatform || 'cvat';
    }

    function applyPlatformLabels() {
      // Platform select is only for Reset / status — dedicated Push/Open buttons stay fixed.
    }

    function setOpenLink(el, url) {
      if (!el) return;
      if (url) {
        el.href = url;
        el.style.pointerEvents = 'auto';
        el.style.opacity = '1';
      } else {
        el.href = '#';
        el.style.pointerEvents = 'none';
        el.style.opacity = '0.45';
      }
    }

    function latestHandoff(entities, platform) {
      const tasks = (entities && entities.tasks) || [];
      const match = [...tasks].reverse().find(t => (t.external_platform || '') === platform && t.handoff_url);
      return match ? match.handoff_url : null;
    }

    document.getElementById('platform-select').value = selectedPlatform;
    document.getElementById('platform-select').addEventListener('change', (e) => {
      selectedPlatform = e.target.value;
      localStorage.setItem('demo_platform', selectedPlatform);
      applyPlatformLabels();
      refreshState().then(updateUi).catch(() => {});
    });
    applyPlatformLabels();
    setOpenLink(document.getElementById('btn-open-ls'), null);
    setOpenLink(document.getElementById('btn-open-cvat'), null);

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

    function stateFlowHtml(steps, currentKey, labelOverrides) {
      const idx = steps.findIndex(s => s.key === currentKey);
      const active = idx < 0 ? 0 : idx;
      const overrides = labelOverrides || {};
      return `<div class="state-flow">${steps.map((s, i) => {
        let cls = 'upcoming';
        if (i < active) cls = 'done';
        else if (i === active) cls = 'current';
        const label = overrides[s.key] || s.label;
        const arrow = i < steps.length - 1 ? '<div class="flow-arrow">↓</div>' : '';
        return `<div class="flow-step ${cls}">${label}</div>${arrow}`;
      }).join('')}</div>`;
    }

    const IMAGE_FLOW = [
      { key: 'ingested', label: 'ingested' },
      { key: 'in_ls_triage', label: 'LS triage' },
      { key: 'ls_labeled', label: 'LS labeled' },
      { key: 'in_cvat', label: 'in CVAT' },
      { key: 'cvat_labeled', label: 'CVAT labeled' },
    ];
    const LS_TASK_FLOW = [
      { key: 'created', label: 'created' },
      { key: 'triaged', label: 'LS labeled' },
    ];
    const CVAT_TASK_FLOW = [
      { key: 'created', label: 'created' },
      { key: 'annotated', label: 'CVAT labeled' },
    ];

    function imageFlowKey(workflowState) {
      const s = workflowState || 'ingested';
      if (['validated', 'ingested'].includes(s)) return 'ingested';
      if (s === 'in_ls_triage') return 'in_ls_triage';
      if (['triaged', 'triaged_valid', 'rejected_at_triage', 'triage_unsure'].includes(s)) return 'ls_labeled';
      if (['queued_for_annotation', 'in_cvat'].includes(s)) return 'in_cvat';
      if (s === 'annotated') return 'cvat_labeled';
      return 'ingested';
    }

    function imageFlowOverrides(workflowState) {
      const s = workflowState || '';
      if (s === 'triaged_valid') return { ls_labeled: 'LS labeled · valid' };
      if (s === 'rejected_at_triage') return { ls_labeled: 'LS labeled · invalid' };
      if (s === 'triage_unsure') return { ls_labeled: 'LS labeled · unsure' };
      if (s === 'validated') return { ingested: 'validated' };
      return {};
    }

    function imageBody(workflowState) {
      const s = workflowState || 'ingested';
      const map = {
        ingested: 'Ingested from Kobo (or dummy seed) — stored in Orchestrator only.',
        validated: 'Registered locally — ready for LS / CVAT push.',
        in_ls_triage: 'Pushed to Label Studio — waiting for triage webhook.',
        triaged_valid: 'LS labeled valid_weed — ready for manual Push to CVAT.',
        rejected_at_triage: 'LS labeled invalid — pipeline can stop here.',
        triage_unsure: 'LS labeled unsure — needs review before CVAT.',
        triaged: 'LS triage imported via webhook/poller.',
        queued_for_annotation: 'Queued in CVAT for bbox annotation.',
        in_cvat: 'In CVAT — waiting for job completed.',
        annotated: 'CVAT labeled — COCO labels imported into Orchestrator.',
      };
      return map[s] || `workflow_state=${s}`;
    }

    function isLsAnnotation(r) {
      const plat = (r.external_platform || '').toLowerCase();
      const fmt = (r.export_format || '').toUpperCase();
      if (plat === 'label_studio') return true;
      if (fmt.includes('COCO')) return false;
      if (fmt === 'JSON' || fmt.includes('LS')) return true;
      return (r.task_ref || '').startsWith('LS-');
    }

    function latestTask(tasks, platform) {
      return [...(tasks || [])].reverse().find(t =>
        (t.external_platform || 'cvat') === platform
      ) || null;
    }

    function taskSummaryHtml(r, platform) {
      if (!r) return '';
      const isLs = platform === 'label_studio';
      const flow = isLs ? LS_TASK_FLOW : CVAT_TASK_FLOW;
      let statusKey = r.status || 'created';
      if (isLs && statusKey === 'annotated') statusKey = 'triaged';
      if (!isLs && statusKey === 'triaged') statusKey = 'annotated';
      if (!flow.some(s => s.key === statusKey)) statusKey = 'created';
      let body = isLs
        ? `LS task #${r.external_task_id || '—'} · triage handoff.`
        : `CVAT task #${r.external_task_id || '—'} · bbox annotation handoff.`;
      if (isLs && (r.status === 'triaged' || r.status === 'annotated')) {
        body = `LS task #${r.external_task_id || '—'} · triage imported (LS labeled).`;
      } else if (!isLs && r.status === 'annotated') {
        body = `CVAT task #${r.external_task_id || '—'} · COCO imported (CVAT labeled).`;
      }
      const badge = isLs
        ? '<span class="plat-badge ls">LS</span>'
        : '<span class="plat-badge cvat">CVAT</span>';
      return `
        <div class="ann-block">
          <div class="title">${r.task_ref}${badge}</div>
          ${stateFlowHtml(flow, statusKey)}
          <div class="body">${body}</div>
          <div class="when">${shortTime(r.created_at)}</div>
        </div>
      `;
    }

    function annBlockHtml(r) {
      const ls = isLsAnnotation(r);
      const badge = ls
        ? '<span class="plat-badge ls">LS triage</span>'
        : '<span class="plat-badge cvat">CVAT labels</span>';
      const kind = ls ? 'Label Studio triage choice' : 'CVAT bbox / polygon';
      const fmt = r.export_format || (ls ? 'JSON' : 'COCO');
      return `
        <div class="ann-block">
          <div class="title">${r.citizen_ai_image_id}${badge}</div>
          <div class="body">${kind} · ${r.labels_count ?? '?'} label(s) · ${fmt}
            <br/>task ${r.task_ref || '—'}</div>
          <div class="when">${shortTime(r.created_at)}</div>
        </div>
      `;
    }

    function imageSummaryHtml(r) {
      const flowKey = imageFlowKey(r.workflow_state);
      return `
        <div class="title">${r.citizen_ai_image_id}</div>
        ${stateFlowHtml(IMAGE_FLOW, flowKey, imageFlowOverrides(r.workflow_state))}
        <div class="body">${imageBody(r.workflow_state)}</div>
        <div class="when">${shortTime(r.created_at)}</div>
      `;
    }

    function flowRank(workflowState) {
      const order = ['ingested', 'in_ls_triage', 'ls_labeled', 'in_cvat', 'cvat_labeled'];
      const key = imageFlowKey(workflowState);
      const idx = order.indexOf(key);
      return idx < 0 ? 0 : idx;
    }

    /** One focus image for Entity summaries — furthest along the pipeline. */
    function pickFocusImage(imgs) {
      if (!imgs.length) return null;
      return [...imgs].sort((a, b) => {
        const d = flowRank(b.workflow_state) - flowRank(a.workflow_state);
        if (d !== 0) return d;
        return String(b.created_at || '').localeCompare(String(a.created_at || ''));
      })[0];
    }

    function renderSummaries() {
      const e = entities;
      const imgs = e.images || [];
      const tasks = e.tasks || [];
      const links = e.task_images || [];
      const anns = e.annotations || [];
      const focus = pickFocusImage(imgs);
      const focusId = focus ? focus.citizen_ai_image_id : null;
      const focusLinks = focusId
        ? links.filter(l => l.citizen_ai_image_id === focusId)
        : [];
      const focusTaskRefs = new Set(focusLinks.map(l => l.task_ref));
      const focusTasks = tasks.filter(t => focusTaskRefs.has(t.task_ref));
      const focusAnns = focusId
        ? anns.filter(a => a.citizen_ai_image_id === focusId)
        : [];

      if (!focus) {
        setSummary('summary-image', '<div class="empty">—</div>');
      } else {
        setSummary('summary-image', imageSummaryHtml(focus));
      }

      const lsTask = latestTask(focusTasks, 'label_studio');
      const cvatTask = latestTask(focusTasks, 'cvat');
      if (!lsTask && !cvatTask) {
        setSummary('summary-task', focus
          ? '<div class="empty">No task yet for this image — Push to LS / CVAT</div>'
          : '<div class="empty">—</div>');
      } else {
        setSummary('summary-task',
          taskSummaryHtml(lsTask, 'label_studio') + taskSummaryHtml(cvatTask, 'cvat')
        );
      }

      if (!focusLinks.length) {
        setSummary('summary-task-image', '<div class="empty">—</div>');
      } else {
        const blocks = focusLinks.map(r => {
          const plat = (r.task_ref || '').startsWith('LS-') ? 'ls' : 'cvat';
          const badge = plat === 'ls'
            ? '<span class="plat-badge ls">LS</span>'
            : '<span class="plat-badge cvat">CVAT</span>';
          return `
            <div class="ann-block">
              <div class="title">${r.task_ref} ↔ ${r.citizen_ai_image_id}${badge}</div>
              <div class="body">Junction row — unchanged after pull.</div>
            </div>
          `;
        }).join('');
        setSummary('summary-task-image', blocks);
      }

      if (!focusAnns.length) {
        const waiting = focusTasks.length
          ? '<div class="empty">Not yet — label in LS or complete CVAT job</div>'
          : '<div class="empty">—</div>';
        setSummary('summary-annotation', waiting);
      } else {
        const lsAnns = focusAnns.filter(isLsAnnotation);
        const cvatAnns = focusAnns.filter(a => !isLsAnnotation(a));
        const blocks = [];
        if (lsAnns.length) blocks.push(annBlockHtml(lsAnns[lsAnns.length - 1]));
        if (cvatAnns.length) blocks.push(annBlockHtml(cvatAnns[cvatAnns.length - 1]));
        if (!blocks.length) blocks.push(annBlockHtml(focusAnns[focusAnns.length - 1]));
        setSummary('summary-annotation', blocks.join(''));
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
            ['platform', r.external_platform || 'cvat'],
            ['external_task_id', r.external_task_id],
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
        ? e.annotations.map(r => {
            const ls = isLsAnnotation(r);
            const kind = ls ? 'LS triage' : 'CVAT labels';
            return rowHtml('annotations', r, [
              ['Annotation', r.citizen_ai_image_id],
              ['kind', kind],
              ['platform', r.external_platform || (ls ? 'label_studio' : 'cvat')],
              ['labels', r.labels_count ?? '?'],
              ['export_format', r.export_format],
              ['task_ref', r.task_ref],
              ['created_at', r.created_at],
            ]);
          }).join('')
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
          ${item.how ? `<div class="how">${item.how}</div>` : ''}
          ${item.how_detail ? `<div class="how-detail">${item.how_detail}</div>` : ''}
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
      const dataHandoff = payload.data && payload.data.handoff_url;
      let lsUrl = latestHandoff(entities, 'label_studio');
      let cvatUrl = latestHandoff(entities, 'cvat');
      if (payload.lastAction === 'push_ls' && dataHandoff) lsUrl = dataHandoff;
      if (payload.lastAction === 'push_cvat' && dataHandoff) cvatUrl = dataHandoff;
      const linkEl = document.getElementById('handoff-link');
      const shown = (payload.lastAction === 'push_cvat' ? cvatUrl : null)
        || (payload.lastAction === 'push_ls' ? lsUrl : null)
        || lsUrl || cvatUrl || task?.handoff_url;
      if (shown) {
        linkEl.innerHTML = `<a href="${shown}" target="_blank" rel="noopener">${shown}</a>`;
      } else {
        linkEl.textContent = '— (push to LS or CVAT first)';
      }
      setOpenLink(document.getElementById('btn-open-ls'), lsUrl);
      setOpenLink(document.getElementById('btn-open-cvat'), cvatUrl);
      const anns = entities.annotations || [];
      const lsAnns = anns.filter(isLsAnnotation);
      const cvatAnns = anns.filter(a => !isLsAnnotation(a));
      const lsN = lsAnns.length ? (lsAnns[lsAnns.length - 1].labels_count ?? 0) : null;
      const cvatN = cvatAnns.length ? (cvatAnns[cvatAnns.length - 1].labels_count ?? 0) : null;
      const badge = document.getElementById('labels-badge');
      const big = document.getElementById('labels-big');
      if (lsN !== null || cvatN !== null) {
        const parts = [];
        if (lsN !== null) parts.push(`LS ${lsN}`);
        if (cvatN !== null) parts.push(`CVAT ${cvatN}`);
        badge.textContent = parts.join(' · ');
        badge.className = 'badge ok';
        big.textContent = parts.join(' · ');
      } else {
        const labels = state.task?.annotations?.[0]?.labels_count;
        if (labels !== undefined && labels !== null) {
          badge.textContent = String(labels);
          badge.className = 'badge ' + (labels > 0 ? 'ok' : 'warn');
          big.textContent = labels > 0 ? `labels = ${labels}` : '';
        }
      }
      renderEntities();
      renderSummaries();
      renderActivity();
      if (payload.changes) applyHighlightKeys(payload.changes);
    }

    async function refreshState() {
      const res = await fetch('/api/state?platform=' + encodeURIComponent(currentPlatform()));
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
      const body = { platform: currentPlatform() };
      if (action === 'pull_kobo') {
        body.limit = 1;
        body.auto_push_ls = false;
      }
      if (action === 'push_ls') {
        selectedPlatform = 'label_studio';
        localStorage.setItem('demo_platform', selectedPlatform);
        document.getElementById('platform-select').value = selectedPlatform;
        applyPlatformLabels();
      }
      if (action === 'push_cvat') {
        selectedPlatform = 'cvat';
        localStorage.setItem('demo_platform', selectedPlatform);
        document.getElementById('platform-select').value = selectedPlatform;
        applyPlatformLabels();
      }
      const opts = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        };
        const res = await fetch('/api/' + action, action === 'status'
          ? { method: 'GET', headers: { 'X-Demo-Platform': currentPlatform() } }
          : opts);
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Request failed');
        out.textContent = JSON.stringify(data.data, null, 2);
        updateUi({ ...data, lastAction: action });
      } catch (err) {
        out.textContent = String(err);
        out.style.color = '#fca5a5';
        const when = new Date().toLocaleTimeString();
        activityLog.push({
          when,
          action,
          entity: 'Error',
          summary: `${action} failed`,
          detail: String(err),
          how: 'No DB rows written for this action',
          how_detail: 'Check platform is up and you are logged in (use an external browser for CVAT).',
        });
        renderActivity();
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
        const res = await fetch('/api/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ platform: currentPlatform() }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Reset failed');
        activityLog = [];
        seenWebhookEventIds.clear();
        lastWebhookFeedId = 0;
        document.getElementById('labels-big').textContent = '';
        document.getElementById('labels-badge').textContent = '—';
        document.getElementById('labels-badge').className = 'badge';
        document.getElementById('handoff-link').textContent = '—';
        setOpenLink(document.getElementById('btn-open-ls'), null);
        setOpenLink(document.getElementById('btn-open-cvat'), null);
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
        // If server feed was cleared (Reset) but this tab kept a high cursor, resync.
        const feedAll = data.state?.webhook_feed || [];
        if (feedAll.length) {
          const maxId = Math.max(...feedAll.map(e => Number(e.id) || 0));
          if (lastWebhookFeedId > maxId) {
            lastWebhookFeedId = 0;
            seenWebhookEventIds.clear();
          }
        } else if (lastWebhookFeedId > 0 && !events.length) {
          lastWebhookFeedId = 0;
          seenWebhookEventIds.clear();
        }
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
        // Always refresh entities/state — pull may have happened while the tab
        // missed the feed event (background throttle / stale after_id).
        if (data.state || data.entities) {
          updateUi({ state: data.state, entities: data.entities });
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


def _state_payload(platform: str | None = None) -> dict:
    plat = normalize_platform(platform or SETTINGS.platform)
    state = demo_state(SETTINGS, platform=plat)
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
    return jsonify(_state_payload(_request_platform()))


@app.post("/api/init")
def api_init():
    try:
        plat = _request_platform()
        data = init_poc(SETTINGS)
        try:
            if plat == "label_studio":
                wh = register_ls_webhook(target_url=DEFAULT_LS_WEBHOOK_TARGET, settings=SETTINGS)
            else:
                wh = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
            data["webhook"] = wh
        except Exception as wh_exc:  # noqa: BLE001
            data["webhook"] = {"error": str(wh_exc), "is_active": False}
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("init", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/seed")
def api_seed():
    try:
        plat = _request_platform()
        data = seed_image(
            image_id=DEMO_IMAGE_ID,
            path=DEMO_IMAGE_PATH,
            metadata=DEMO_METADATA,
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("seed", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/pull_kobo")
def api_pull_kobo():
    try:
        body = request.get_json(silent=True) or {}
        limit = int(body.get("limit") or 1)
        auto_push = body.get("auto_push_ls", False)
        if isinstance(auto_push, str):
            auto_push = auto_push.lower() in ("1", "true", "yes")
        data = pull_from_kobo(limit=limit, auto_push_ls=bool(auto_push), settings=SETTINGS)
        state = demo_state(SETTINGS, platform=_request_platform())
        return jsonify(_full_payload("pull_kobo", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/push_ls")
def api_push_ls():
    try:
        body = request.get_json(silent=True) or {}
        image_id = body.get("image_id") or None
        data = push_latest_to_ls(image_id=image_id, settings=SETTINGS)
        state = demo_state(SETTINGS, platform="label_studio")
        return jsonify(_full_payload("push_ls", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/push_cvat")
def api_push_cvat():
    try:
        body = request.get_json(silent=True) or {}
        image_id = body.get("image_id") or None
        data = push_latest_to_cvat(image_id=image_id, settings=SETTINGS)
        if data.get("handoff_url"):
            data = {**data, "handoff_url": data["handoff_url"]}
        state = demo_state(SETTINGS, platform="cvat")
        return jsonify(_full_payload("push_cvat", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/push")
def api_push():
    try:
        plat = _request_platform()
        data = push_to_platform(
            image_id=DEMO_IMAGE_ID,
            task_ref=next_demo_task_ref(SETTINGS),
            platform=plat,
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("push", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/pull")
def api_pull():
    try:
        body = request.get_json(silent=True) or {}
        plat = _request_platform()
        data = pull_from_platform(
            task_ref=active_demo_task_ref(SETTINGS),
            force=bool(body.get("force")),
            platform=plat,
            settings=SETTINGS,
        )
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("pull", data, state))
    except PocError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/reset")
def api_reset():
    try:
        plat = _request_platform()
        data = reset_all(SETTINGS, platform=plat)
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("reset", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.post("/api/webhooks/register")
def api_webhooks_register():
    try:
        plat = _request_platform()
        if plat == "label_studio":
            data = register_ls_webhook(target_url=DEFAULT_LS_WEBHOOK_TARGET, settings=SETTINGS)
        else:
            data = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("register_webhook", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.get("/api/webhooks/feed")
def api_webhooks_feed():
    try:
        after_id = int(request.args.get("after_id") or 0)
        events = get_webhook_feed(after_id=after_id)
        plat = _request_platform()
        state = demo_state(SETTINGS, platform=plat)
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


@app.post("/api/webhooks/label_studio")
def api_webhooks_label_studio():
    """Label Studio → Orchestrator webhook receiver."""
    try:
        payload = request.get_json(silent=True) or {}
        result = handle_ls_webhook(payload, settings=SETTINGS, force_pull=True)
        return jsonify({"ok": True, "result": result}), 200
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/webhooks")
def api_webhooks_list():
    try:
        plat = _request_platform()
        if plat == "label_studio":
            data = list_ls_webhooks(SETTINGS)
        else:
            data = list_cvat_webhooks(SETTINGS)
        return jsonify({"ok": True, "data": data, "platform": plat})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


@app.get("/review/activity-log")
def review_activity_log():
    """Non-operational before/after copy review for activity-log text."""
    return render_template_string(REVIEW_PAGE, cases=build_review_cases())


@app.get("/api/status")
def api_status():
    try:
        plat = request.headers.get("X-Demo-Platform") or request.args.get("platform") or SETTINGS.platform
        plat = normalize_platform(plat)
        data = get_status(task_ref=active_demo_task_ref(SETTINGS), settings=SETTINGS)
        state = demo_state(SETTINGS, platform=plat)
        return jsonify(_full_payload("status", data, state))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    init_poc(SETTINGS)
    print("Demo UI: http://127.0.0.1:5050")
    print(f"Next demo task ref: {next_demo_task_ref(SETTINGS)}")
    print(f"CVAT webhook target: {DEFAULT_WEBHOOK_TARGET}")
    print(f"LS webhook target: {DEFAULT_LS_WEBHOOK_TARGET}")
    try:
        wh = register_cvat_webhook(target_url=DEFAULT_WEBHOOK_TARGET, settings=SETTINGS)
        print(f"CVAT webhook always-on: #{wh.get('webhook_id')} → {wh.get('target_url')}")
    except Exception as exc:  # noqa: BLE001
        print(f"CVAT webhook auto-register skipped: {exc}")
    try:
        wh = register_ls_webhook(target_url=DEFAULT_LS_WEBHOOK_TARGET, settings=SETTINGS)
        print(f"LS webhook always-on: #{wh.get('webhook_id')} → {wh.get('target_url')}")
    except Exception as exc:  # noqa: BLE001
        print(f"LS webhook auto-register skipped: {exc}")

    def _poll_loop() -> None:
        time.sleep(3)
        while True:
            try:
                poll_completed_jobs(settings=SETTINGS, force_pull=False)
            except Exception as exc:  # noqa: BLE001
                print(f"[poller-cvat] {exc}")
            try:
                poll_ls_completed_tasks(settings=SETTINGS, force_pull=False)
            except Exception as exc:  # noqa: BLE001
                print(f"[poller-ls] {exc}")
            time.sleep(4)

    threading.Thread(target=_poll_loop, name="annotation-complete-poller", daemon=True).start()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
