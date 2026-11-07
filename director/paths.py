"""Repository paths and .env loading (no third-party dotenv dependency)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "shared"
SCHEMA_DIR = SHARED / "schema"
PLAN_SCHEMA_PATH = SCHEMA_DIR / "scene_plan_v1.json"
RESULT_SCHEMA_PATH = SCHEMA_DIR / "scene_result_v1.json"
INBOX = SHARED / "inbox"
OUTBOX = SHARED / "outbox"
STATE = SHARED / "state"
LATEST_MANIFEST = STATE / "latest_manifest.json"
TRACES = STATE / "traces"


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from the repo-root .env without overriding real env vars.

    The .env file is gitignored. Values are never printed.
    """
    path = path or (REPO_ROOT / ".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
