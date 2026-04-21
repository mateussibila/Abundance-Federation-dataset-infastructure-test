# Pilot 01: Volunteer-Built Agricultural AI Dataset

## What This Is

A micro-pilot testing whether structured volunteer processes can produce AI-ready image datasets that meet academic standards. We are starting with Irish weed identification for precision agriculture.

**Duration:** 12 weeks (April–June 2026)  
**Team:** 4 volunteer data scientists (L1–L4) + coordinator  
**Oversight:** Prof. of Statistics (TCD), Prof. of Computer Science (UCD)

## Current Focus (Weeks 2–3)

Two paired teams working in parallel:

| Team | Focus | Members |
|------|-------|---------|
| **Team A** | Data Collection & Ingestion | 1 × L3–4 + 1 × L1–2 |
| **Team B** | Annotation Platform | 1 × L3–4 + 1 × L1–2 |

**Status:** Structure and design phase — no production data yet.

QA scripting and analysis are parked until domain experts and real data are available. Design docs are being prepared now.

## Volunteer Levels

| Level | Title | Description |
|-------|-------|-------------|
| L1 | Observer / Beginner | Follow written instructions, use web tools, report observations |
| L2 | Contributor | Work independently on structured tasks, handle data files, write docs |
| L3 | Practitioner | Write scripts, deploy tools, design protocols, coach L1–2 volunteers |
| L4 | Steward | Design and run pilots independently, mentor L3 volunteers |

## Repository Structure

- `protocols/` — Data collection and annotation protocols
- `scripts/ingestion/` — (to be built) KoboToolbox export, rename, and upload scripts
- `scripts/setup/` — Environment setup, Label Studio configuration, and deployment scripts
- `scripts/annotation/` — Annotation-related utilities and support scripts
- `scripts/qa/` — Gold tracker and agreement tracker scripts (parked)
- `notebooks/` — Analysis and ML notebooks (parked)
- `docs/` — Meeting notes, decisions log, training materials
- `templates/` — Dataset creation templates and reusable examples

**Note:** Empty folders are maintained using `.gitkeep` files until working files are added.

## Key Documents

- [Pilot 01 Outline](link-to-google-doc) — What we are building and why
- [Tooling & Infrastructure Plan](link-to-google-doc) — How we implement it
- [Crowdsourcing Weed Data](link-to-google-doc) — Background research on data quality
- [Two-Week Sprint Plan](link-to-google-doc) — Detailed steps for Weeks 2–3

## Getting Started

1. Read the key documents linked above
2. Check the [Issues](../../issues) tab for your team’s tasks
3. Filter issues by label: `team:collection` or `team:annotation`
4. Update your issue with progress comments as you work

## Team

| Role | Person | Level |
|------|--------|-------|
| Coordinator | [Rick] | — |
| Team A – L3–4 | TBD | L3–4 |
| Team A – L1–2 | TBD | L1–2 |
| Team B – L3–4 | TBD | L3–4 |
| Team B – L1–2 | TBD | L1–2 |

## First Tasks

If you are new, start with:
- Read the key documents
- Check the Issues tab
- Confirm which team you are on
- Leave a short progress update on your assigned task

## Decisions Log

Key decisions are recorded in `docs/decisions.md`.
