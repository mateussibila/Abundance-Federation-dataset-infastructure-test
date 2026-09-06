# Abundance Federation — dataset infrastructure (sandbox)

Personal / team **sandbox** for Orchestrator POCs, CVAT/Label Studio experiments, and throwaway tests.

Official pilot docs and production-facing code live in the org repo:  
[`Abundance-Federation/dataset-infastructure`](https://github.com/Abundance-Federation/dataset-infastructure).

## What’s here

| Path | Role |
|------|------|
| [`sandbox/orchestrator_poc/`](sandbox/orchestrator_poc/) | Orchestrator POC (Flask demo, adapters, SQLite) |
| [`docs/orchestrator/`](docs/orchestrator/) | POC design notes and stack sketches |
| [`sandbox/`](sandbox/) | Docker Compose helpers, dummy images, config *examples* |

## Reproduce the local POC

See [`sandbox/orchestrator_poc/README.md`](sandbox/orchestrator_poc/README.md).

Copy `sandbox/*.example.json` → real config files locally (gitignored; never commit tokens/passwords).
