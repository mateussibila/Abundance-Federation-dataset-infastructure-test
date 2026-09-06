"""KoboToolbox REST adapter for the Orchestrator POC (pull submissions + media)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

# Pilot dual-photo slots (filename column, URL column, suffix for citizen id).
PHOTO_SLOTS = (
    ("_45_degree_angle_photo", "_45_degree_angle_photo_URL", "45deg"),
    ("Top_down_photo", "Top_down_photo_URL", "topdown"),
)

DEFAULT_START_DATE = "2026-06-25"


@dataclass
class KoboConfig:
    base_url: str
    token: str
    asset_uid: str
    start_date: str = DEFAULT_START_DATE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KoboConfig":
        return cls(
            base_url=str(data.get("base_url") or "https://eu.kobotoolbox.org").rstrip("/"),
            token=str(data.get("token") or "").strip(),
            asset_uid=str(data.get("asset_uid") or data.get("form_uid") or "").strip(),
            start_date=str(data.get("start_date") or DEFAULT_START_DATE).strip()[:10],
        )

    def headers(self) -> dict[str, str]:
        if not self.token or self.token.startswith("REPLACE_"):
            raise ValueError(
                "Kobo token missing. Copy sandbox/kobo-sandbox-info.json.example to "
                "kobo-sandbox-info.json and set token + asset_uid."
            )
        if not self.asset_uid or self.asset_uid.startswith("REPLACE_"):
            raise ValueError("Kobo asset_uid missing in kobo-sandbox-info.json")
        return {"Authorization": f"Token {self.token}"}


class KoboClient:
    def __init__(self, config: KoboConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.headers())

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return urljoin(self.config.base_url + "/", path.lstrip("/"))

    def iter_submissions_newest_first(
        self,
        *,
        start_date: str | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        All submissions on/after start_date, newest first (_submission_time descending).
        """
        start = (start_date or self.config.start_date or DEFAULT_START_DATE)[:10]
        query = json.dumps({"_submission_time": {"$gte": f"{start}T00:00:00"}})
        sort = '{"_submission_time": -1}'
        out: list[dict[str, Any]] = []
        start_offset = 0
        while True:
            r = self.session.get(
                self._url(f"/api/v2/assets/{self.config.asset_uid}/data/"),
                params={
                    "limit": page_size,
                    "start": start_offset,
                    "sort": sort,
                    "query": query,
                },
                timeout=90,
            )
            r.raise_for_status()
            body = r.json()
            results = body.get("results") if isinstance(body, dict) else body
            if not isinstance(results, list) or not results:
                break
            out.extend(results)
            if len(results) < page_size:
                break
            start_offset += len(results)
            if start_offset > 5000:
                break
        return out

    def next_submission_after(
        self,
        *,
        already_pulled_ids: set[str],
        start_date: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the newest eligible submission not yet in already_pulled_ids."""
        for sub in self.iter_submissions_newest_first(start_date=start_date):
            sid = str(sub.get("_id") or sub.get("id") or "")
            if not sid:
                continue
            if sid in already_pulled_ids:
                continue
            return sub
        return None

    def resolve_media_url(self, submission: dict[str, Any], filename: str, url_hint: str) -> str:
        hint = (url_hint or "").strip()
        if hint.startswith("http"):
            return hint.rstrip("/")
        for att in submission.get("_attachments") or []:
            download = (
                att.get("download_url")
                or att.get("download_large_url")
                or att.get("filename")
                or ""
            )
            media = att.get("mimetype") or ""
            fname = str(att.get("filename") or download).split("/")[-1]
            if filename and fname.endswith(filename):
                if str(download).startswith("http"):
                    return str(download).rstrip("/")
            if filename and filename in str(download):
                if str(download).startswith("http"):
                    return str(download).rstrip("/")
            if "image" in str(media) and str(download).startswith("http") and not filename:
                return str(download).rstrip("/")
        if hint:
            return self._url(hint)
        raise FileNotFoundError(f"No media URL for {filename or 'attachment'}")

    def download_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self.session.get(url.rstrip("/"), timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest


def extract_photo_candidates(submission: dict[str, Any]) -> list[dict[str, str]]:
    """Return list of {filename, url, slot} for photos on a submission."""
    found: list[dict[str, str]] = []
    for filename_col, url_col, slot in PHOTO_SLOTS:
        filename = str(submission.get(filename_col) or "").strip()
        url = str(submission.get(url_col) or "").strip()
        if filename or url:
            if not filename and url:
                filename = urlparse(url).path.split("/")[-1] or f"{slot}.jpg"
            found.append({"filename": filename, "url": url, "slot": slot})
    if found:
        return found

    for att in submission.get("_attachments") or []:
        media = str(att.get("mimetype") or "")
        download = att.get("download_url") or att.get("download_large_url") or ""
        if "image" not in media and not str(download).lower().endswith(
            (".jpg", ".jpeg", ".png")
        ):
            continue
        fname = str(att.get("filename") or download).split("/")[-1] or "photo.jpg"
        fname = fname.split("/")[-1]
        if str(download).startswith("http"):
            found.append({"filename": fname, "url": str(download), "slot": "photo"})
            break
    return found


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())[:64]
    return cleaned or "img"
