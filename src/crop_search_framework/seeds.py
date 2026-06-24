"""Phase C / WS-6 — curated source seed registry.

Seeds move out of run configs into ``config/seeds/<crop>.json`` with verified
scope, source quality, and caveats. ``seeds_for_run`` expands the registry into
the inline ``source_seeds`` shape the exploration runner already understands, so
the rest of the pipeline is unchanged. ``validate_seeds`` schema-checks the
registry, confirms every covered parameter id exists in the manifest, and
(optionally) probes for dead links.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema_registry import SchemaRegistry

INLINE_SEED_KEYS = ("source_url", "title", "snippet", "document_type", "source_tier_id", "parameter_ids")


def load_seed_registry(repo_root: Path, registry_path: str) -> Dict[str, Any]:
    path = Path(registry_path)
    if not path.is_absolute():
        path = repo_root / path
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    SchemaRegistry(repo_root).validate("seed-registry.schema.json", payload)
    return payload


def _seed_matches(seed: Dict[str, Any], selector: Dict[str, Any]) -> bool:
    for field in ("crop", "region", "source_tier_id"):
        wanted = selector.get(field)
        if wanted and seed.get(field) != wanted:
            return False
    tiers = selector.get("source_tier_ids")
    if tiers and seed.get("source_tier_id") not in set(tiers):
        return False
    return True


def to_inline_seed(seed: Dict[str, Any]) -> Dict[str, Any]:
    """Map a registry seed to the run-config inline ``source_seeds`` shape."""
    return {
        "source_url": seed["source_url"],
        "title": seed.get("title", seed["source_url"]),
        "snippet": seed.get("snippet", ""),
        "document_type": seed.get("document_type", "html"),
        "source_tier_id": seed.get("source_tier_id", ""),
        "parameter_ids": list(seed.get("covered_parameters", [])),
    }


def seeds_for_run(repo_root: Path, run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return inline seeds for a run.

    Precedence: explicit inline ``source_seeds`` win (back-compat); otherwise
    expand the referenced registry filtered by ``seed_selector``.
    """
    if run_config.get("source_seeds"):
        return run_config["source_seeds"]
    registry_path = run_config.get("seed_registry_path")
    if not registry_path:
        return []
    registry = load_seed_registry(repo_root, registry_path)
    selector = run_config.get("seed_selector", {})
    return [to_inline_seed(seed) for seed in registry["seeds"] if _seed_matches(seed, selector)]


def validate_seeds(
    repo_root: Path,
    registry_path: str,
    manifest_path: str,
    check_links: bool = False,
) -> Dict[str, Any]:
    registry = load_seed_registry(repo_root, registry_path)
    manifest = _load_manifest(repo_root, manifest_path)
    known_ids = {p["parameter_id"] for p in manifest.get("parameters", [])}

    unknown_params: List[Dict[str, Any]] = []
    dead_links: List[Dict[str, Any]] = []
    for seed in registry["seeds"]:
        for pid in seed.get("covered_parameters", []):
            if pid not in known_ids:
                unknown_params.append({"seed_id": seed["seed_id"], "parameter_id": pid})
        if check_links:
            status = _probe(seed["source_url"])
            if status is None or status >= 400:
                dead_links.append({"seed_id": seed["seed_id"], "source_url": seed["source_url"], "status": status})

    return {
        "registry_path": registry_path,
        "seed_count": len(registry["seeds"]),
        "unknown_parameter_refs": unknown_params,
        "dead_links": dead_links,
        "valid": not unknown_params and not dead_links,
        "links_checked": check_links,
    }


def _load_manifest(repo_root: Path, manifest_path: str) -> Dict[str, Any]:
    path = Path(manifest_path)
    if not path.is_absolute():
        path = repo_root / path
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _probe(url: str) -> Optional[int]:
    from .dev_tools.http_client import HttpClient, HttpError

    client = HttpClient(max_retries=2, sleeper=lambda s: None)
    try:
        return client.get_binary(url, timeout=15).status_code
    except HttpError:
        return 599
    except Exception:
        return None
