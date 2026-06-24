from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def fixture_path() -> Path:
    return repo_root() / "fixtures" / "pilot_sources.json"


def user_agent() -> str:
    return os.environ.get(
        "CROP_SEARCH_USER_AGENT",
        "crop-search-foundation/0.1 (+https://example.local/crop-search)",
    )


def load_request() -> Dict[str, Any]:
    return json.load(sys.stdin)


def load_fixtures() -> Dict[str, Any]:
    with fixture_path().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def emit_response(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout)
