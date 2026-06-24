from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from .schema_registry import SchemaRegistry


class ToolManifest:
    def __init__(self, repo_root: Path, manifest_path: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        self.manifest_path = manifest_path
        self.payload = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.registry.validate("mcp-server-manifest.schema.json", payload)
        return payload

    def server_config(self, server_name: str) -> Dict[str, Any]:
        for server in self.payload["servers"]:
            if server["name"] == server_name:
                return server
        raise KeyError("Unknown server binding: {0}".format(server_name))


class CommandToolRunner:
    def __init__(self, repo_root: Path, manifest_path: Path) -> None:
        self.repo_root = repo_root
        self.manifest = ToolManifest(repo_root, manifest_path)

    def invoke(self, server_name: str, request: Dict[str, Any]) -> Dict[str, Any]:
        server = self.manifest.server_config(server_name)
        executable = sys.executable if server["command"] in {"python", "python3"} else server["command"]
        command: List[str] = [executable] + server.get("args", [])
        env = None
        if server.get("env"):
            env = os.environ.copy()
            env.update(server["env"])
        completed = subprocess.run(
            command,
            input=json.dumps(request).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.repo_root),
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Tool {0} failed with code {1}: {2}".format(
                    server_name, completed.returncode, stderr or "no stderr"
                )
            )
        response_text = completed.stdout.decode("utf-8").strip()
        if not response_text:
            raise ValueError("Tool {0} returned empty output".format(server_name))
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Tool {0} returned invalid JSON: {1}".format(server_name, response_text)
            ) from exc
