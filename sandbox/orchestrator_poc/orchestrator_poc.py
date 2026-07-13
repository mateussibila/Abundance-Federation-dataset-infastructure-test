#!/usr/bin/env python3
"""Orchestrator CVAT POC — push one image, pull COCO export, store Annotation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from poc_service import (
    PocError,
    PocSettings,
    get_status,
    init_poc,
    pull_from_cvat,
    push_to_cvat,
    seed_image,
)

POC_DIR = Path(__file__).resolve().parent
DEFAULT_DB = POC_DIR / "poc.db"
DEFAULT_CONFIG = POC_DIR.parent / "cvat-sandbox-info.json"


def _settings(args: argparse.Namespace) -> PocSettings:
    return PocSettings(db_path=Path(args.db), config_path=Path(args.config))


def cmd_init_db(args: argparse.Namespace) -> int:
    data = init_poc(_settings(args))
    print(f"Initialized database at {data['db_path']}")
    return 0


def cmd_seed_image(args: argparse.Namespace) -> int:
    metadata = {}
    if args.farm:
        metadata["farm"] = args.farm
    if args.volunteer_id:
        metadata["volunteer_id"] = args.volunteer_id
    if args.photo_type:
        metadata["photo_type"] = args.photo_type
    try:
        data = seed_image(
            image_id=args.image_id,
            path=args.path,
            metadata=metadata,
            settings=_settings(args),
        )
    except PocError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Seeded image {data['image_id']} (id={data['orchestrator_image_id']})")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    try:
        data = push_to_cvat(
            image_id=args.image_id,
            task_ref=args.task_ref,
            settings=_settings(args),
        )
    except PocError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    try:
        data = pull_from_cvat(
            task_ref=args.task_ref,
            force=args.force,
            settings=_settings(args),
        )
    except PocError as exc:
        print(exc, file=sys.stderr)
        return 1
    for item in data["images"]:
        if item.get("skipped"):
            print(f"Skipping {item['citizen_ai_image_id']}: active annotation already exists")
        else:
            print(
                f"Stored annotation {item['annotation_id']} for {item['citizen_ai_image_id']} "
                f"({item['labels_count']} labels)"
            )
    print(
        f"Pull complete: created={data['created']}, skipped={data['skipped']}, "
        f"raw_export={data['raw_export_path']}"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    data = get_status(task_ref=args.task_ref, settings=_settings(args))
    if not data["tasks"]:
        print("No tasks found.")
        return 0
    for item in data["tasks"]:
        task = item["task"]
        print(f"Task {task['task_ref']} ({task['status']})")
        print(f"  orchestrator_id: {task['id']}")
        print(f"  cvat_task_id:    {task['external_task_id']}")
        print(f"  handoff_url:     {task['handoff_url']}")
        for image in item["images"]:
            print(
                f"  image: {image['citizen_ai_image_id']} "
                f"workflow_state={image['workflow_state']}"
            )
        for ann in item["annotations"]:
            print(
                f"  annotation: {ann['citizen_ai_image_id']} labels={ann['labels_count']}"
            )
        live = item.get("cvat_live")
        if live and "error" not in live:
            print(f"  cvat_live: size={live.get('size')} jobs={live.get('jobs')}")
        elif live:
            print(f"  cvat_live: unavailable ({live['error']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orchestrator CVAT POC")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="CVAT sandbox config JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create SQLite schema and export directories")

    seed = sub.add_parser("seed-image", help="Register one image in Orchestrator DB")
    seed.add_argument("--image-id", required=True, help="citizen_ai_image_id")
    seed.add_argument("--path", required=True, help="Local image path")
    seed.add_argument("--farm", default=None)
    seed.add_argument("--volunteer-id", default=None)
    seed.add_argument("--photo-type", default=None)

    push = sub.add_parser("push", help="Create CVAT task and upload one image")
    push.add_argument("--image-id", required=True)
    push.add_argument("--task-ref", required=True, help="Orchestrator task reference, e.g. TASK-0001")

    pull = sub.add_parser("pull", help="Export COCO annotations from CVAT and normalize")
    pull.add_argument("--task-ref", required=True)
    pull.add_argument("--force", action="store_true", help="Replace existing active annotation")

    status = sub.add_parser("status", help="Show Orchestrator task/image/annotation state")
    status.add_argument("--task-ref", default=None)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    commands = {
        "init-db": cmd_init_db,
        "seed-image": cmd_seed_image,
        "push": cmd_push,
        "pull": cmd_pull,
        "status": cmd_status,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
