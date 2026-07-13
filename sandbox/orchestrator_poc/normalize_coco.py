"""Normalize COCO 1.0 exports into Orchestrator Annotation.payload."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any


def load_coco_from_export(export_path: Path) -> dict[str, Any]:
    if export_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(export_path) as zf:
            json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
            if not json_names:
                raise ValueError(f"No JSON file found in export zip: {export_path}")
            with zf.open(json_names[0]) as handle:
                return json.load(handle)
    return json.loads(export_path.read_text(encoding="utf-8"))


def _stem(filename: str) -> str:
    return Path(filename).stem


def _category_map(coco: dict[str, Any]) -> dict[int, str]:
    return {cat["id"]: cat["name"] for cat in coco.get("categories", [])}


def _segmentation_to_points(segmentation: list[Any]) -> list[list[float]]:
    if not segmentation:
        return []
    ring = segmentation[0]
    if not isinstance(ring, list) or len(ring) < 6:
        return []
    pairs = []
    for i in range(0, len(ring) - 1, 2):
        pairs.append([float(ring[i]), float(ring[i + 1])])
    return pairs


def normalize_coco_for_image(
    coco: dict[str, Any],
    *,
    image_ref: str,
    external_task_id: str,
    export_format: str = "COCO 1.0",
) -> dict[str, Any]:
    categories = _category_map(coco)
    image_rows = [
        img for img in coco.get("images", []) if _stem(img.get("file_name", "")) == image_ref
    ]
    if not image_rows:
        available = [_stem(img.get("file_name", "")) for img in coco.get("images", [])]
        raise ValueError(
            f"Image ref {image_ref!r} not found in COCO export. Available: {available}"
        )

    image_id = image_rows[0]["id"]
    labels: list[dict[str, Any]] = []

    for ann in coco.get("annotations", []):
        if ann.get("image_id") != image_id:
            continue
        name = categories.get(ann.get("category_id"), "unknown")
        segmentation = ann.get("segmentation") or []
        if segmentation:
            points = _segmentation_to_points(segmentation)
            if points:
                labels.append(
                    {
                        "name": name,
                        "geometry_type": "polygon",
                        "points": points,
                    }
                )
                continue
        bbox = ann.get("bbox")
        if bbox and len(bbox) == 4:
            x, y, w, h = bbox
            labels.append(
                {
                    "name": name,
                    "geometry_type": "bbox",
                    "points": [[float(x), float(y), float(x + w), float(y + h)]],
                }
            )

    return {
        "schema_version": "1",
        "source_platform": "cvat",
        "export_format": export_format,
        "external_task_id": str(external_task_id),
        "image_ref": image_ref,
        "labels": labels,
    }


def save_normalized_payload(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def extract_json_bytes_from_zip(data: bytes) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
        if not json_names:
            raise ValueError("Export zip contains no JSON file")
        name = json_names[0]
        with zf.open(name) as handle:
            return name, json.load(handle)
