from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema_registry import SchemaRegistry


class HookRunner:
    def __init__(self, repo_root: Path, config_path: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        self.config = self._load_config(config_path)

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        with config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def run_event(self, event: Dict[str, Any]) -> List[str]:
        self.registry.validate("hook-event.schema.json", event)
        actions = self.config.get("hooks", {}).get(event["event_name"], [])
        results: List[str] = []
        for action in actions:
            result = self._run_action(action, event)
            if result:
                results.append(result)
        return results

    def _run_action(self, action: Dict[str, Any], event: Dict[str, Any]) -> Optional[str]:
        action_type = action["action"]
        if action_type == "log":
            return (
                f"logged {event['event_name']} for run {event['run_id']} "
                f"at {event['occurred_at']}"
            )
        if action_type == "validate_schema":
            schema_name = action["schema"]
            self.registry.validate(schema_name, event["payload"])
            return f"validated payload against {schema_name}"
        if action_type == "write_artifact":
            output_dir = self.repo_root / action["output_dir"]
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output_path = output_dir / f"{event['run_id']}-{event['event_name']}-{timestamp}.json"
            with output_path.open("w", encoding="utf-8") as handle:
                json.dump(event, handle, indent=2)
                handle.write("\n")
            return f"wrote artifact to {output_path.relative_to(self.repo_root)}"
        raise ValueError(f"Unsupported hook action: {action_type}")
