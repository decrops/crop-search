from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from jsonschema import Draft202012Validator, FormatChecker


class SchemaRegistry:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.schema_dir = repo_root / "schemas"

    def load_schema(self, schema_name_or_path: str) -> Dict[str, Any]:
        path = Path(schema_name_or_path)
        schema_path = path if path.is_absolute() else self.schema_dir / path.name
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {schema_name_or_path}")
        with schema_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def validate(self, schema_name_or_path: str, payload: Dict[str, Any]) -> None:
        schema = self.load_schema(schema_name_or_path)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(payload), key=lambda err: err.path)
        if errors:
            rendered = "; ".join(
                f"{'.'.join(str(part) for part in err.path) or '<root>'}: {err.message}"
                for err in errors
            )
            raise ValueError(rendered)
