"""Normalize Label Studio annotation JSON into Orchestrator Annotation.payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _rect_to_bbox_points(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    original_width: float,
    original_height: float,
) -> list[list[float]]:
    """Label Studio rectangle values are percentages of image size."""
    x1 = (x / 100.0) * original_width
    y1 = (y / 100.0) * original_height
    x2 = ((x + width) / 100.0) * original_width
    y2 = ((y + height) / 100.0) * original_height
    return [[float(x1), float(y1), float(x2), float(y2)]]


def normalize_ls_annotations_for_image(
    annotations: list[dict[str, Any]],
    *,
    image_ref: str,
    external_task_id: str,
    export_format: str = "JSON",
) -> dict[str, Any]:
    """
    Convert Label Studio task.annotations[] into the same payload shape as COCO normalize.
    Uses the latest annotation (highest id) when several exist.
    Supports triage Choices and rectanglelabels.
    """
    labels: list[dict[str, Any]] = []
    if not annotations:
        return {
            "schema_version": "1",
            "source_platform": "label_studio",
            "export_format": export_format,
            "external_task_id": str(external_task_id),
            "image_ref": image_ref,
            "labels": labels,
            "triage": None,
        }

    chosen = sorted(annotations, key=lambda a: int(a.get("id") or 0))[-1]
    triage: str | None = None
    for item in chosen.get("result") or []:
        item_type = item.get("type") or ""
        value = item.get("value") or {}

        if item_type in ("choices", "choice"):
            names = value.get("choices") or value.get("choice") or []
            if isinstance(names, str):
                names = [names]
            name = str(names[0]) if names else "unknown"
            triage = name
            labels.append(
                {
                    "name": name,
                    "geometry_type": "classification",
                    "points": [],
                }
            )
            continue

        if item_type not in ("rectanglelabels", "rectangle"):
            continue
        names = value.get("rectanglelabels") or value.get("labels") or []
        name = names[0] if names else "unknown"
        if triage is None:
            triage = str(name)
        ow = float(item.get("original_width") or value.get("original_width") or 0)
        oh = float(item.get("original_height") or value.get("original_height") or 0)
        if ow <= 0 or oh <= 0:
            continue
        points = _rect_to_bbox_points(
            x=float(value.get("x") or 0),
            y=float(value.get("y") or 0),
            width=float(value.get("width") or 0),
            height=float(value.get("height") or 0),
            original_width=ow,
            original_height=oh,
        )
        labels.append(
            {
                "name": name,
                "geometry_type": "bbox",
                "points": points,
            }
        )

    return {
        "schema_version": "1",
        "source_platform": "label_studio",
        "export_format": export_format,
        "external_task_id": str(external_task_id),
        "image_ref": image_ref,
        "labels": labels,
        "triage": triage,
    }


def triage_decision(payload: dict[str, Any]) -> str:
    """
    Return valid | rejected | unsure | unknown from a normalized LS payload.
    """
    valid = frozenset({"valid_weed", "is_weed", "weed", "valid"})
    rejected = frozenset({"invalid", "not_weed", "reject", "rejected"})

    raw = payload.get("triage")
    names = [str(raw)] if raw else []
    for lab in payload.get("labels") or []:
        names.append(str(lab.get("name") or ""))
    lowered = [n.strip().lower() for n in names if n]
    for n in lowered:
        if n in valid:
            return "valid"
        if n in rejected:
            return "rejected"
        if n == "unsure":
            return "unsure"
    return "unknown"


def image_ref_from_task(task: dict[str, Any]) -> str | None:
    meta = task.get("meta") or {}
    if meta.get("citizen_ai_image_id"):
        return str(meta["citizen_ai_image_id"])
    data = task.get("data") or {}
    image = data.get("image") or ""
    if not image:
        return None
    # URL or path — take stem of last path segment (strip query).
    name = str(image).split("?")[0].rstrip("/").split("/")[-1]
    return Path(name).stem or None
