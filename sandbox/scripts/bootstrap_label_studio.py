#!/usr/bin/env python3
"""Bootstrap Label Studio: wait → signup/login → token → ensure project → write info JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator_poc"))

from label_studio_adapter import LabelStudioClient, LabelStudioConfig  # noqa: E402

INFO_PATH = ROOT / "label-studio-sandbox-info.json"


def main() -> int:
    data = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    config = LabelStudioConfig.from_dict(data)
    client = LabelStudioClient(config)
    print(f"Waiting for Label Studio at {config.base_url} …")
    client.wait_until_ready(timeout_s=240)
    print("Ready. Signing up / logging in …")
    token = client.signup_or_login()
    print(f"Token acquired ({token[:8]}…)")
    project_id = client.ensure_project()
    print(f"Project id={project_id} title={config.project_title!r}")

    data["api_token"] = token
    data["project_id"] = project_id
    data["base_url"] = config.base_url
    data["public_base_url"] = config.browser_base_url
    INFO_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {INFO_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
