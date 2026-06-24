from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .schema_registry import SchemaRegistry
from .source_tiers import selected_source_tiers


@dataclass(frozen=True)
class QueryPlanItem:
    query: str
    parameter_id: str
    parameter_family: str
    parameter_label: str
    source_tier_id: str = ""
    source_tier_label: str = ""


def load_parameter_manifest(repo_root: Path, manifest_path: str) -> Dict[str, Any]:
    path = resolve_repo_path(repo_root, manifest_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    SchemaRegistry(repo_root).validate("parameter-manifest.schema.json", payload)
    return payload


def load_crop_profile(repo_root: Path, crop_profile_path: str) -> Dict[str, Any]:
    path = resolve_repo_path(repo_root, crop_profile_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    SchemaRegistry(repo_root).validate("crop-profile.schema.json", payload)
    return payload


def resolve_repo_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def query_plan_for_run(repo_root: Path, run_config: Dict[str, Any]) -> List[QueryPlanItem]:
    if run_config.get("parameter_manifest_path"):
        manifest = load_parameter_manifest(repo_root, run_config["parameter_manifest_path"])
        crop_profile = load_crop_profile(repo_root, run_config["crop_profile_path"])
        return generate_parameter_queries(repo_root, run_config, manifest, crop_profile)
    return [
        QueryPlanItem(
            query=query,
            parameter_id="",
            parameter_family="",
            parameter_label="",
            source_tier_id="",
            source_tier_label="",
        )
        for query in run_config.get("queries", [])
    ]


def selected_parameters(run_config: Dict[str, Any], manifest: Dict[str, Any], crop_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit_ids = set(run_config.get("parameter_ids", []))
    selected_families = set(run_config.get("parameter_families", []))
    crop_group = crop_profile["crop_group"]
    selected = []
    for parameter in manifest["parameters"]:
        if parameter.get("implementation_status", "active") != "active":
            continue
        if explicit_ids and parameter["parameter_id"] not in explicit_ids:
            continue
        if selected_families and parameter["family"] not in selected_families:
            continue
        applies_to = set(parameter.get("applies_to_crop_groups", []))
        if applies_to and crop_group not in applies_to:
            continue
        selected.append(parameter)

    max_parameters = run_config.get("max_parameters")
    if max_parameters:
        return selected[: int(max_parameters)]
    return selected


def load_query_templates(repo_root: Path, run_config: Dict[str, Any]) -> Dict[str, Any]:
    """Load the parameter-aware query template config (WS-5).

    Optional: if the file is missing, return an empty config and the query
    builder falls back to its prior generic behavior.
    """
    path_value = run_config.get("query_template_path", "config/query-templates/default.json")
    path = resolve_repo_path(repo_root, path_value)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def generate_parameter_queries(
    repo_root: Path,
    run_config: Dict[str, Any],
    manifest: Dict[str, Any],
    crop_profile: Dict[str, Any],
) -> List[QueryPlanItem]:
    crop_term = crop_profile["aliases"][0]
    scientific_names = crop_profile.get("scientific_names") or []
    region_name = run_config["region_scope"]["name"]
    query_limit = int(run_config.get("queries_per_parameter", 1))
    query_terms_per_tier = int(run_config.get("query_terms_per_source_tier", 3))
    source_tiers = selected_source_tiers(repo_root, run_config)
    templates = load_query_templates(repo_root, run_config)
    plan: List[QueryPlanItem] = []
    for parameter in selected_parameters(run_config, manifest, crop_profile):
        patterns = parameter.get("evidence_patterns") or parameter["search_aliases"]
        for pattern in patterns[:query_limit]:
            rendered = pattern.replace("{crop}", crop_term)
            if source_tiers:
                for source_tier in source_tiers:
                    plan.append(
                        QueryPlanItem(
                            query=build_query(
                                rendered,
                                region_name,
                                source_tier["query_terms"][:query_terms_per_tier],
                                parameter,
                                templates=templates,
                                source_tier_id=source_tier["tier_id"],
                                scientific_names=scientific_names,
                            ),
                            parameter_id=parameter["parameter_id"],
                            parameter_family=parameter["family"],
                            parameter_label=parameter["label"],
                            source_tier_id=source_tier["tier_id"],
                            source_tier_label=source_tier["label"],
                        )
                    )
                continue
            plan.append(
                QueryPlanItem(
                    query=build_query(
                        rendered,
                        region_name,
                        ["extension", "agronomy"],
                        parameter,
                        templates=templates,
                        source_tier_id="",
                        scientific_names=scientific_names,
                    ),
                    parameter_id=parameter["parameter_id"],
                    parameter_family=parameter["family"],
                    parameter_label=parameter["label"],
                )
            )
    return plan


def parameter_query_vocab(parameter: Dict[str, Any], templates: Dict[str, Any]):
    """Resolve (units, terms) for a parameter. Param-level fields override the
    domain defaults from the template config."""
    domain_config = (templates.get("domains") or {}).get(
        parameter.get("domain", ""), templates.get("default_domain", {})
    )
    units = parameter.get("query_units") or domain_config.get("units", [])
    terms = parameter.get("query_terms") or domain_config.get("terms", [])
    return units, terms


def build_query(
    rendered_pattern: str,
    region_name: str,
    source_terms: List[str],
    parameter: Dict[str, Any],
    templates: Dict[str, Any] = None,
    source_tier_id: str = "",
    scientific_names: List[str] = None,
) -> str:
    templates = templates or {}
    scientific_names = scientific_names or []
    query_parts = [rendered_pattern]

    # Scholarly tiers get the scientific name (reusing the crop profile field).
    scholarly = source_tier_id in set(templates.get("scholarly_tiers", []))
    if scholarly and scientific_names:
        query_parts.append(scientific_names[0])

    if region_name.lower() != "global":
        query_parts.append(region_name)
    query_parts.extend(source_terms)

    units, domain_terms = parameter_query_vocab(parameter, templates)
    # Parameter/domain idiom (e.g. "N rate", "crop coefficient").
    query_parts.extend(domain_terms[:2])
    # Stage vocabulary only for stage-dependent params, under the query budget.
    if parameter.get("requires_stage_context") and templates.get("stage_terms"):
        query_parts.append(templates["stage_terms"][0])

    if parameter["value_type"] in {"numeric", "range", "numeric_or_range", "text_or_numeric", "numeric_or_text"}:
        if units:
            # Replace the blunt "value" token with parameter-specific units.
            query_parts.extend(units[:2])
        elif not templates:
            query_parts.append("value")
    return dedupe_words(" ".join(query_parts))


def parameter_by_subtype(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        parameter["normalized_attribute_subtype"]: parameter
        for parameter in manifest["parameters"]
    }


def parameter_by_id(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        parameter["parameter_id"]: parameter
        for parameter in manifest["parameters"]
    }


def dedupe_words(value: str) -> str:
    words = value.split()
    deduped = []
    previous = ""
    for word in words:
        if word.lower() == previous.lower():
            continue
        deduped.append(word)
        previous = word
    return " ".join(deduped)
