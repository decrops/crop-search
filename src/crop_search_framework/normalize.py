from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .geocoding import county_scope_from_text, enrich_scope, production_region_scope_from_text
from .hooks import HookRunner
from .parameters import load_parameter_manifest, parameter_by_id, parameter_by_subtype
from .quality import (
    capture_has_crop_signal,
    capture_has_preferred_domain,
    capture_has_preferred_source_term,
    capture_relevance_score,
    claim_has_layout_artifact,
    claim_has_source_header_artifact,
)
from .schema_registry import SchemaRegistry


ATTRIBUTE_RULES = [
    ("gdu_requirement", "threshold", ("gdu", "growing degree unit", "heat unit")),
    ("soil_temperature_threshold", "threshold", ("soil temperature", "germination", "50 degrees", "10 degrees")),
    ("evapotranspiration_requirement", "condition", ("evapotranspiration",)),
    ("soil_water_storage", "condition", ("soil profile", "hold", "inches of water")),
    ("water_table_depth", "condition", ("water table", "feet depth")),
    ("preferred_temperature", "condition", ("warm", "temperature", "heat", "growth")),
    ("moisture_requirement", "condition", ("moisture", "water stress", "water", "drought", "drainage")),
    ("soil_drainage_requirement", "recommendation", ("well-drained", "drainage", "standing water", "saturated soil")),
    ("planting_window", "timing", ("planting", "date", "window", "silking", "maturity")),
]

BANNED_CLAIM_PATTERNS = (
    "table of contents",
    "acknowledgements",
    "author acknowledgements",
    "cooperative extension service",
    "equal opportunity provider",
    "all rights reserved",
    "more information about",
    "can be found on the worldwide web",
    "program discrimination complaint",
    "date of planting 101-day",
    "h20 yield",
    "lsd0.05",
    "least significant difference",
    "leaf collars",
    "seminal roots",
    "nodal roots",
    "radicle",
    "coleoptile",
)

US_STATE_ALIASES = {
    "Alabama": ("alabama", "AL"),
    "Alaska": ("alaska", "AK"),
    "Arizona": ("arizona", "AZ"),
    "Arkansas": ("arkansas", "AR"),
    "California": ("california", "CA"),
    "Colorado": ("colorado", "CO"),
    "Connecticut": ("connecticut", "CT"),
    "Delaware": ("delaware", "DE"),
    "Florida": ("florida", "FL"),
    "Georgia": ("georgia", "GA"),
    "Idaho": ("idaho", "ID"),
    "Illinois": ("illinois", "IL"),
    "Indiana": ("indiana", "IN"),
    "Iowa": ("iowa", "IA"),
    "Kansas": ("kansas", "KS"),
    "Kentucky": ("kentucky", "KY"),
    "Louisiana": ("louisiana", "LA"),
    "Maine": ("maine", "ME"),
    "Maryland": ("maryland", "MD"),
    "Massachusetts": ("massachusetts", "MA"),
    "Michigan": ("michigan", "MI"),
    "Minnesota": ("minnesota", "MN"),
    "Mississippi": ("mississippi", "MS"),
    "Missouri": ("missouri", "MO"),
    "Montana": ("montana", "MT"),
    "Nebraska": ("nebraska", "NE"),
    "Nevada": ("nevada", "NV"),
    "New Hampshire": ("new hampshire", "NH"),
    "New Jersey": ("new jersey", "NJ"),
    "New Mexico": ("new mexico", "NM"),
    "New York": ("new york", "NY"),
    "North Carolina": ("north carolina", "NC"),
    "North Dakota": ("north dakota", "ND"),
    "Ohio": ("ohio", "OH"),
    "Oklahoma": ("oklahoma", "OK"),
    "Oregon": ("oregon", "OR"),
    "Pennsylvania": ("pennsylvania", "PA"),
    "South Carolina": ("south carolina", "SC"),
    "South Dakota": ("south dakota", "SD"),
    "Tennessee": ("tennessee", "TN"),
    "Texas": ("texas", "TX"),
    "Utah": ("utah", "UT"),
    "Vermont": ("vermont", "VT"),
    "Virginia": ("virginia", "VA"),
    "Washington": ("washington", "WA"),
    "West Virginia": ("west virginia", "WV"),
    "Wisconsin": ("wisconsin", "WI"),
    "Wyoming": ("wyoming", "WY"),
}

BROAD_COUNTRY_PATTERNS = (
    r"\bacross the u\.?s\.?\b",
    r"\bthe u\.?s\.?\b",
    r"\bunited states\b",
    r"\bnational\b",
)


class NormalizationRunner:
    def __init__(self, repo_root: Path, hook_config_path: Path) -> None:
        self.repo_root = repo_root
        self.registry = SchemaRegistry(repo_root)
        self.hooks = HookRunner(repo_root, hook_config_path)

    def normalize_run(self, run_id: str) -> Dict[str, Any]:
        raw_dir = self.repo_root / "exploration" / "raw" / run_id
        normalized_dir = self.repo_root / "exploration" / "normalized" / run_id
        normalized_dir.mkdir(parents=True, exist_ok=True)
        run_context = self._run_context(run_id, raw_dir)
        crop = run_context["crop"]

        for stale_claim in normalized_dir.glob("*.json"):
            if stale_claim.name != "summary.json":
                stale_claim.unlink()

        claim_files = sorted(path for path in raw_dir.glob("*.json") if path.name != "summary.json")
        normalized_claims: List[Dict[str, Any]] = []
        skipped_capture_count = 0
        skipped_claim_count = 0

        for claim_file in claim_files:
            capture = self._load_json(claim_file)
            self.registry.validate("raw-capture.schema.json", capture)
            if not is_relevant_capture(capture, crop):
                skipped_capture_count += 1
                continue

            for index, claim_text in enumerate(capture.get("candidate_claims", []), start=1):
                if not should_normalize_claim(claim_text, capture, crop):
                    skipped_claim_count += 1
                    continue
                pre_normalize_event = self._hook_event(
                    "pre-normalize",
                    run_id,
                    {"capture_id": capture["id"], "claim_text": claim_text},
                )
                self.hooks.run_event(pre_normalize_event)
                normalized_claims.append(self._normalize_claim(capture, claim_text, index, run_context))

        conflict_summary = apply_conflict_flags(normalized_claims)
        output_files: List[str] = []

        for normalized in normalized_claims:
            self.registry.validate("normalized-claim.schema.json", normalized)
            pre_load_event = self._hook_event("pre-load", run_id, normalized)
            self.hooks.run_event(pre_load_event)

            output_path = normalized_dir / "{0}.json".format(normalized["claim_id"])
            with output_path.open("w", encoding="utf-8") as handle:
                json.dump(normalized, handle, indent=2)
                handle.write("\n")
            output_files.append(str(output_path.relative_to(self.repo_root)))

        summary = {
            "run_id": run_id,
            "normalized_claims": len(normalized_claims),
            "skipped_captures": skipped_capture_count,
            "skipped_claims": skipped_claim_count,
            "potential_conflict_groups": conflict_summary["groups"],
            "claims_with_potential_conflicts": conflict_summary["claims"],
            "output_dir": str(normalized_dir.relative_to(self.repo_root)),
            "files": output_files,
        }
        summary_path = normalized_dir / "summary.json"
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
            handle.write("\n")
        return summary

    def _normalize_claim(
        self,
        capture: Dict[str, Any],
        claim_text: str,
        index: int,
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        attribute, attribute_subtype, observation_type = infer_attribute_metadata(claim_text)
        value = extract_value(claim_text, attribute)
        claim_geo = infer_claim_geo_scope(claim_text, capture, run_context["region_scope"])
        source_geo = infer_source_geo_scope(capture, run_context["region_scope"])
        provenance = {
            "source_urls": [capture.get("final_url") or capture["source_url"]],
            "source_title": capture.get("source_title") or capture.get("search_title") or capture["source_url"],
            "source_domain": capture.get("source_domain") or "",
            "document_type": capture["document_type"],
            "source_tier_id": capture.get("source_tier_id", ""),
            "source_tier_label": capture.get("source_tier_label", ""),
            "discovery_method": capture.get("discovery_method", ""),
            "access_status": capture.get("access_status", "unknown"),
            "accessed_at": capture["accessed_at"],
            "extraction_method": "heuristic normalization from raw capture",
            "evidence_text": first_evidence_fragment(capture, claim_text),
            "notes": capture.get("publication_date_hint", ""),
        }
        source_publication_date = parse_publication_date(capture.get("publication_date_hint", ""))
        source_publication_year = parse_publication_year(capture.get("publication_date_hint", ""))
        if source_publication_date:
            provenance["source_publication_date"] = source_publication_date
        if source_publication_year is not None:
            provenance["source_publication_year"] = source_publication_year
        return {
            "claim_id": "{0}-claim-{1:03d}".format(capture["id"], index),
            "run_id": capture["run_id"],
            "entity": {
                "entity_type": "crop",
                "name": run_context["crop"],
            },
            "parameter_id": parameter_id_for_subtype(attribute_subtype, run_context),
            "attribute": attribute,
            "attribute_subtype": attribute_subtype,
            "claim_text": claim_text,
            "value": value,
            "location_scope": claim_geo["scope"],
            "source_geo_scope": source_geo["scope"],
            "geo_evidence": build_geo_evidence(claim_geo, source_geo),
            "time_scope": infer_time_scope(attribute, claim_text),
            "provenance": provenance,
            "observation_type": observation_type,
            "confidence": infer_confidence(capture, value, run_context["crop"]),
            "conflict_status": "none",
            "status": "load_ready",
        }

    def normalize_run_from_llm(
        self,
        run_id: str,
        backend: Any,
        output_subdir: str = "normalized",
    ) -> Dict[str, Any]:
        """Normalize a run's raw captures via the LLM extractor backend.

        Each extraction dict becomes a normalized claim (geo scopes reused from the
        heuristic path; agronomic_scope / bbch_applicability / manifest_version added),
        then claims are deduped/merged on the combined applicability key.
        """
        raw_dir = self.repo_root / "exploration" / "raw" / run_id
        out_dir = self.repo_root / "exploration" / output_subdir / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        run_context = self._run_context(run_id, raw_dir)
        crop = run_context["crop"]
        active_params = run_context["active_parameters"]

        for stale in out_dir.glob("*.json"):
            if stale.name != "summary.json":
                stale.unlink()

        capture_files = sorted(p for p in raw_dir.glob("*.json") if p.name != "summary.json")
        raw_claims: List[Dict[str, Any]] = []
        for capture_file in capture_files:
            capture = self._load_json(capture_file)
            self.registry.validate("raw-capture.schema.json", capture)
            for extraction in backend.extract(capture, crop, active_params):
                claim = self._claim_from_extraction(capture, extraction, run_context, backend.name)
                if claim is not None:
                    raw_claims.append(claim)

        merged = merge_extraction_claims(raw_claims, run_id)
        conflict_summary = apply_conflict_flags(merged)

        output_files: List[str] = []
        for claim in merged:
            self.registry.validate("normalized-claim.schema.json", claim)
            output_path = out_dir / "{0}.json".format(claim["claim_id"])
            with output_path.open("w", encoding="utf-8") as handle:
                json.dump(claim, handle, indent=2)
                handle.write("\n")
            output_files.append(str(output_path.relative_to(self.repo_root)))

        summary = {
            "run_id": run_id,
            "backend": backend.name,
            "extraction_source": "llm",
            "manifest_version": run_context["manifest_version"],
            "raw_extracted_claims": len(raw_claims),
            "normalized_claims": len(merged),
            "merged_away": len(raw_claims) - len(merged),
            "potential_conflict_groups": conflict_summary["groups"],
            "claims_with_potential_conflicts": conflict_summary["claims"],
            "output_dir": str(out_dir.relative_to(self.repo_root)),
            "files": output_files,
        }
        summary_path = out_dir / "summary.json"
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
            handle.write("\n")
        return summary

    def _claim_from_extraction(
        self,
        capture: Dict[str, Any],
        extraction: Dict[str, Any],
        run_context: Dict[str, Any],
        backend_name: str,
    ) -> Optional[Dict[str, Any]]:
        parameter_id = extraction["parameter_id"]
        param = run_context.get("parameter_by_id", {}).get(parameter_id, {})
        attribute = param.get("normalized_attribute") or parameter_id.split(".", 1)[0]
        attribute_subtype = param.get("normalized_attribute_subtype") or parameter_id.split(".", 1)[-1]
        evidence = extraction.get("evidence_text", "").strip()
        claim_text = (extraction.get("claim_summary") or evidence).strip()
        if len(claim_text) < 5 or not evidence:
            return None

        value = value_from_extraction(extraction)
        claim_geo = infer_claim_geo_scope(evidence, capture, run_context["region_scope"])
        source_geo = infer_source_geo_scope(capture, run_context["region_scope"])
        provenance = {
            "source_urls": [capture.get("final_url") or capture["source_url"]],
            "source_title": capture.get("source_title") or capture.get("search_title") or capture["source_url"],
            "source_domain": capture.get("source_domain") or "",
            "document_type": capture["document_type"],
            "source_tier_id": capture.get("source_tier_id", ""),
            "source_tier_label": capture.get("source_tier_label", ""),
            "discovery_method": capture.get("discovery_method", ""),
            "access_status": capture.get("access_status", "unknown"),
            "accessed_at": capture["accessed_at"],
            "extraction_method": "llm:{0}".format(backend_name),
            "evidence_text": evidence,
            "manifest_version": run_context.get("manifest_version", ""),
            "notes": capture.get("publication_date_hint", ""),
        }
        source_publication_date = parse_publication_date(capture.get("publication_date_hint", ""))
        source_publication_year = parse_publication_year(capture.get("publication_date_hint", ""))
        if source_publication_date:
            provenance["source_publication_date"] = source_publication_date
        if source_publication_year is not None:
            provenance["source_publication_year"] = source_publication_year
        for key in (
            "organisms",
            "method",
            "price_year",
            "currency",
            "area_unit",
            "document_id",
            "block_anchor",
            "block_type",
            "page",
            "table_label",
        ):
            extra_value = extraction.get(key)
            if extra_value not in (None, "", []):
                provenance[key] = extra_value

        claim = {
            "claim_id": "pending",
            "run_id": capture["run_id"],
            "entity": {"entity_type": "crop", "name": run_context["crop"]},
            "parameter_id": parameter_id,
            "attribute": attribute,
            "attribute_subtype": attribute_subtype,
            "claim_text": claim_text,
            "value": value,
            "location_scope": claim_geo["scope"],
            "source_geo_scope": source_geo["scope"],
            "geo_evidence": build_geo_evidence(claim_geo, source_geo),
            "time_scope": infer_time_scope(attribute, claim_text),
            "provenance": provenance,
            "observation_type": observation_type_from_qualifier(extraction.get("qualifier", "descriptive")),
            "confidence": extraction.get("extraction_confidence") or "low",
            "conflict_status": "none",
            "status": "load_ready",
        }

        agronomic_scope = {}
        if extraction.get("cultivar"):
            agronomic_scope["cultivar"] = extraction["cultivar"]
        if extraction.get("management_system"):
            agronomic_scope["management_system"] = extraction["management_system"]
        if agronomic_scope:
            claim["agronomic_scope"] = agronomic_scope

        if extraction.get("bbch_min") is not None and extraction.get("bbch_max") is not None:
            bbch = {
                "bbch_min": int(extraction["bbch_min"]),
                "bbch_max": int(extraction["bbch_max"]),
                "evidence_text": evidence,
            }
            if extraction.get("extraction_confidence"):
                bbch["confidence"] = extraction["extraction_confidence"]
            claim["bbch_applicability"] = bbch

        return claim

    def _run_context(self, run_id: str, raw_dir: Path) -> Dict[str, Any]:
        raw_summary_path = raw_dir / "summary.json"
        if raw_summary_path.exists():
            raw_summary = self._load_json(raw_summary_path)
        else:
            raw_summary = {}
        run_config = self._run_config_for_id(run_id)
        parameter_manifest_path = raw_summary.get("parameter_manifest_path") or run_config.get("parameter_manifest_path", "")
        parameter_lookup = {}
        parameter_id_lookup = {}
        active_params: List[Dict[str, Any]] = []
        manifest_version = ""
        if parameter_manifest_path:
            manifest = load_parameter_manifest(self.repo_root, parameter_manifest_path)
            parameter_lookup = parameter_by_subtype(manifest)
            parameter_id_lookup = parameter_by_id(manifest)
            manifest_version = manifest.get("manifest_version", "")
            active_params = [
                p
                for p in manifest["parameters"]
                if p.get("implementation_status", "active") == "active"
            ]
        return {
            "crop": raw_summary.get("crop", "corn"),
            "region_scope": raw_summary.get(
                "region_scope",
                run_config.get(
                    "region_scope",
                    {
                        "level": "global",
                        "name": "global",
                    },
                ),
            ),
            "parameter_manifest_path": parameter_manifest_path,
            "parameter_by_subtype": parameter_lookup,
            "parameter_by_id": parameter_id_lookup,
            "active_parameters": active_params,
            "manifest_version": manifest_version,
        }

    def _run_config_for_id(self, run_id: str) -> Dict[str, Any]:
        for path in (self.repo_root / "config" / "runs").glob("*.json"):
            payload = self._load_json(path)
            if payload.get("run_id") == run_id:
                return payload
        return {
            "region_scope": {
                "level": "global",
                "name": "global",
            },
        }

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _hook_event(self, event_name: str, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_name": event_name,
            "run_id": run_id,
            "occurred_at": capture_now(),
            "payload": payload,
            "status": "received",
        }


def infer_attribute_metadata(claim_text: str) -> Tuple[str, str, str]:
    lowered = claim_text.lower()
    if is_soil_emergence_temperature(lowered):
        return "temperature_requirement", "soil_emergence_temperature", "threshold"
    if "base temperature" in lowered or "heat accumulation" in lowered or "degree days" in lowered:
        return "temperature_requirement", "base_temperature", "threshold"
    if "survive" in lowered or "adverse temperatures" in lowered:
        return "temperature_requirement", "survival_temperature", "threshold"
    if "stress" in lowered or "exceed" in lowered or "yield" in lowered and "temperature" in lowered:
        return "temperature_requirement", "stress_temperature", "risk"
    if "average daily temperature" in lowered or "daily temperature" in lowered:
        return "temperature_observation", "ambient_air_temperature", "condition"
    if "germinate" in lowered or "germination" in lowered:
        return "temperature_requirement", "germination_temperature", "threshold"
    if "evapotranspiration" in lowered:
        return "water_requirement", "evapotranspiration_requirement", "condition"
    if "soil profile" in lowered and "water" in lowered:
        return "soil_water_storage", "soil_water_holding_capacity", "condition"
    if "kernel" in lowered and ("water" in lowered or "moisture" in lowered):
        return "kernel_moisture", "kernel_moisture_by_growth_stage", "condition"
    if "soil moisture" in lowered or "top-foot soil moisture" in lowered:
        return "soil_moisture_observation", "soil_moisture_status", "condition"
    if "gdu" in lowered or "growing degree unit" in lowered or "heat unit" in lowered:
        return "heat_unit_requirement", "gdu_accumulation", "threshold"
    if "maturity" in lowered and ("days" in lowered or "months" in lowered):
        return "phenology_timing", "maturity_duration", "timing"
    if "planting" in lowered and ("date" in lowered or "dates" in lowered or "window" in lowered):
        return "planting_window", "planting_date_window", "timing"
    for attribute, observation_type, keywords in ATTRIBUTE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return attribute, default_attribute_subtype(attribute), observation_type
    return "growing_condition", "general_growing_condition", "condition"


def infer_attribute(claim_text: str) -> Tuple[str, str]:
    attribute, _, observation_type = infer_attribute_metadata(claim_text)
    return attribute, observation_type


def is_soil_emergence_temperature(lowered: str) -> bool:
    return (
        ("soil temperature" in lowered or "soil temperatures" in lowered)
        and any(term in lowered for term in ("emerge", "emergence", "planting", "germination"))
    )


def default_attribute_subtype(attribute: str) -> str:
    return {
        "preferred_temperature": "general_temperature_condition",
        "moisture_requirement": "general_moisture_requirement",
        "soil_temperature_threshold": "soil_temperature_threshold",
        "evapotranspiration_requirement": "evapotranspiration_requirement",
        "soil_water_storage": "soil_water_holding_capacity",
        "water_table_depth": "water_table_depth",
        "soil_drainage_requirement": "soil_drainage_requirement",
        "planting_window": "planting_date_window",
        "gdu_requirement": "gdu_accumulation",
    }.get(attribute, attribute)


def parameter_id_for_subtype(attribute_subtype: str, run_context: Dict[str, Any]) -> str:
    parameter = run_context.get("parameter_by_subtype", {}).get(attribute_subtype)
    if parameter:
        return parameter["parameter_id"]
    return DEFAULT_PARAMETER_IDS.get(attribute_subtype, "unmapped.{0}".format(attribute_subtype))


DEFAULT_PARAMETER_IDS = {
    "ambient_air_temperature": "temperature.ambient_air_temperature",
    "base_temperature": "temperature.base_temperature",
    "drought_sensitive_stage": "water.drought_sensitive_stage",
    "evapotranspiration_requirement": "water.evapotranspiration_requirement",
    "frost_sensitive_stage": "stress.frost_risk_stage",
    "gdu_accumulation": "thermal_time.gdu_accumulation",
    "general_temperature_condition": "temperature.optimum_growth_temperature",
    "germination_temperature": "temperature.germination_temperature",
    "harvest_moisture": "harvest.harvest_moisture",
    "kernel_moisture_by_growth_stage": "harvest.kernel_moisture_by_growth_stage",
    "maturity_duration": "phenology.maturity_duration",
    "nitrogen_timing": "nutrients.nitrogen_timing",
    "planting_date_window": "planting.planting_window",
    "rotation_recommendation": "management.rotation_recommendation",
    "seeding_depth": "planting.seeding_depth",
    "seeding_rate": "planting.seeding_rate",
    "soil_drainage_requirement": "soil.drainage_requirement",
    "soil_emergence_temperature": "temperature.soil_emergence_temperature",
    "soil_moisture_status": "water.soil_moisture_status",
    "soil_ph_range": "soil.ph_range",
    "soil_water_holding_capacity": "soil.soil_water_holding_capacity",
    "stress_temperature": "temperature.heat_stress_threshold",
    "survival_temperature": "temperature.survival_temperature",
    "waterlogging_sensitivity": "water.waterlogging_sensitivity",
}


def extract_value(claim_text: str, attribute: str) -> Dict[str, Any]:
    if attribute in ("preferred_temperature", "soil_temperature_threshold", "temperature_requirement", "temperature_observation"):
        temperature_value = extract_temperature_value(claim_text)
        if temperature_value:
            return temperature_value
    if attribute == "water_table_depth":
        water_depth = extract_generic_measurement(claim_text, ("feet", "foot", "ft"))
        if water_depth:
            return water_depth
    if attribute in (
        "soil_water_storage",
        "evapotranspiration_requirement",
        "moisture_requirement",
        "water_requirement",
        "soil_moisture_observation",
    ):
        water_amount = extract_generic_measurement(claim_text, ("inches", "inch"))
        if water_amount:
            return water_amount
    if attribute in ("gdu_requirement", "heat_unit_requirement"):
        gdu_value = extract_generic_measurement(claim_text, ("gdu", "gdus"))
        if gdu_value:
            return gdu_value
    if attribute == "planting_window":
        date_value = extract_date_value(claim_text)
        if date_value:
            return date_value

    lowered = claim_text.lower()
    return {
        "value_type": "text",
        "raw_value_text": claim_text,
        "text_value": claim_text,
        "qualifier": qualitative_qualifier(lowered),
    }


def extract_temperature_value(claim_text: str) -> Optional[Dict[str, Any]]:
    contextual_value = extract_contextual_corn_temperature(claim_text)
    if contextual_value:
        return contextual_value
    range_value = extract_temperature_range_with_trailing_unit(claim_text)
    if range_value:
        return range_value
    mentions = re.findall(
        r"(\d+(?:\.\d+)?)\s*(?:°|degrees?\s*)?(f|c|fahrenheit|celsius)\b",
        claim_text.lower(),
    )
    if not mentions:
        return None

    grouped: Dict[str, List[float]] = {"fahrenheit": [], "celsius": []}
    for value, unit in mentions:
        grouped[normalize_unit(unit)].append(float(value))

    preferred_unit = "celsius" if grouped["celsius"] else "fahrenheit"
    values = grouped[preferred_unit]
    if len(values) >= 2:
        range_min = min(values)
        range_max = max(values)
        normalized_min, normalized_max, normalized_unit = normalize_temperature_range(
            range_min,
            range_max,
            preferred_unit,
        )
        return {
            "value_type": "range",
            "raw_value_text": claim_text,
            "range_min": range_min,
            "range_max": range_max,
            "unit": preferred_unit,
            "normalized_range_min": normalized_min,
            "normalized_range_max": normalized_max,
            "normalized_unit": normalized_unit,
        }

    numeric_value = values[0]
    normalized_value, normalized_unit = normalize_temperature_value(numeric_value, preferred_unit)
    return {
        "value_type": "numeric",
        "raw_value_text": claim_text,
        "numeric_value": numeric_value,
        "unit": preferred_unit,
        "normalized_numeric_value": normalized_value,
        "normalized_unit": normalized_unit,
    }


def extract_contextual_corn_temperature(claim_text: str) -> Optional[Dict[str, Any]]:
    lowered = claim_text.lower()
    if "corn" not in lowered or "base temperature" not in lowered:
        return None
    for segment in re.split(r"\bwhile\b|;", claim_text):
        if "corn" not in segment.lower():
            continue
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:°|degrees?\s*)?(f|c|fahrenheit|celsius)\b",
            segment.lower(),
        )
        if not match:
            continue
        numeric_value = float(match.group(1))
        unit = normalize_unit(match.group(2))
        normalized_value, normalized_unit = normalize_temperature_value(numeric_value, unit)
        return {
            "value_type": "numeric",
            "raw_value_text": match.group(0),
            "numeric_value": numeric_value,
            "unit": unit,
            "normalized_numeric_value": normalized_value,
            "normalized_unit": normalized_unit,
        }
    return None


def extract_temperature_range_with_trailing_unit(claim_text: str) -> Optional[Dict[str, Any]]:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(?:°|degrees?\s*)?(f|c|fahrenheit|celsius)\b",
        claim_text.lower(),
    )
    if not match:
        return None
    range_min = float(match.group(1))
    range_max = float(match.group(2))
    unit = normalize_unit(match.group(3))
    normalized_min, normalized_max, normalized_unit = normalize_temperature_range(range_min, range_max, unit)
    return {
        "value_type": "range",
        "raw_value_text": match.group(0),
        "range_min": range_min,
        "range_max": range_max,
        "unit": unit,
        "normalized_range_min": normalized_min,
        "normalized_range_max": normalized_max,
        "normalized_unit": normalized_unit,
    }


def extract_generic_measurement(claim_text: str, units: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    units_pattern = "|".join(re.escape(unit) for unit in units)
    lowered = claim_text.lower()
    range_match = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|-)\s*(\d[\d,]*(?:\.\d+)?)\s*(" + units_pattern + r")\b",
        lowered,
    )
    if range_match:
        return {
            "value_type": "range",
            "raw_value_text": range_match.group(0),
            "range_min": parse_number(range_match.group(1)),
            "range_max": parse_number(range_match.group(2)),
            "unit": range_match.group(3),
        }

    single_match = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(" + units_pattern + r")\b",
        lowered,
    )
    if single_match:
        return {
            "value_type": "numeric",
            "raw_value_text": single_match.group(0),
            "numeric_value": parse_number(single_match.group(1)),
            "unit": single_match.group(2),
        }
    return None


def extract_date_value(claim_text: str) -> Optional[Dict[str, Any]]:
    months = re.findall(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
        claim_text.lower(),
    )
    if len(months) >= 2:
        value_text = "{0} to {1}".format(months[0], months[-1])
        return {
            "value_type": "text",
            "raw_value_text": value_text,
            "text_value": value_text,
            "qualifier": "window",
        }
    if months:
        return {
            "value_type": "text",
            "raw_value_text": months[0],
            "text_value": months[0],
            "qualifier": "date",
        }
    return None


def normalize_unit(value: str) -> str:
    if value.startswith("f"):
        return "fahrenheit"
    return "celsius"


def parse_number(value: str) -> float:
    return float(value.replace(",", ""))


def normalize_temperature_value(numeric_value: float, unit: str) -> Tuple[float, str]:
    if unit == "fahrenheit":
        return round((numeric_value - 32.0) * 5.0 / 9.0, 2), "celsius"
    return numeric_value, "celsius"


def normalize_temperature_range(min_value: float, max_value: float, unit: str) -> Tuple[float, float, str]:
    min_normalized, normalized_unit = normalize_temperature_value(min_value, unit)
    max_normalized, _ = normalize_temperature_value(max_value, unit)
    return min_normalized, max_normalized, normalized_unit


def qualitative_qualifier(lowered: str) -> str:
    if "best" in lowered or "optimal" in lowered:
        return "optimal"
    if "risk" in lowered or "stress" in lowered:
        return "risk"
    if "should" in lowered or "recommend" in lowered:
        return "recommended"
    return "descriptive"


def first_evidence_fragment(capture: Dict[str, Any], claim_text: str) -> str:
    for fragment in capture.get("evidence_fragments", []):
        if claim_text in fragment or fragment in claim_text:
            return fragment
    return claim_text


def infer_confidence(capture: Dict[str, Any], value: Dict[str, Any], crop: str = "corn") -> str:
    relevance_score = capture_relevance_score(capture, crop)
    if relevance_score < 6:
        return "low"
    if capture["document_type"] == "pdf" and value["value_type"] in {"numeric", "range"}:
        return "high"
    if value["value_type"] == "text" and capture["document_type"] == "pdf":
        return "low"
    if value["value_type"] == "text":
        return "medium"
    return "high"


def should_normalize_claim(claim_text: str, capture: Dict[str, Any], crop: str = "corn") -> bool:
    lowered = claim_text.lower()
    source_title = capture.get("source_title") or capture.get("search_title") or ""
    if any(pattern in lowered for pattern in BANNED_CLAIM_PATTERNS):
        return False
    if claim_has_layout_artifact(claim_text):
        return False
    if claim_has_source_header_artifact(claim_text, source_title):
        return False
    if capture_relevance_score(capture, crop) < 5:
        return False
    words = claim_text.split()
    if len(words) < 6 or len(words) > 45:
        return False
    if "contents" in lowered or "table " in lowered or "figure " in lowered:
        return False
    if re.search(r"\bpages?\s+\d+\b", lowered):
        return False
    if not any(keyword in lowered for _, _, keywords in ATTRIBUTE_RULES for keyword in keywords) and not re.search(r"\d", lowered):
        return False
    return True


def is_relevant_capture(capture: Dict[str, Any], crop: str = "corn") -> bool:
    relevance_score = capture_relevance_score(capture, crop)
    if relevance_score < 5:
        return False
    if capture_has_preferred_domain(capture) or capture_has_preferred_source_term(capture):
        return True
    return capture_has_crop_signal(capture, crop) and relevance_score >= 9


def infer_location_scope(
    claim_text: str,
    capture: Dict[str, Any],
    default_region: Dict[str, str],
) -> Dict[str, str]:
    return infer_claim_geo_scope(claim_text, capture, default_region)["scope"]


def infer_claim_geo_scope(
    claim_text: str,
    capture: Dict[str, Any],
    default_region: Dict[str, str],
) -> Dict[str, Any]:
    source_title = capture.get("source_title") or capture.get("search_title") or ""
    claim_lower = claim_text.lower()
    title_lower = source_title.lower()

    if "research farm" in claim_lower and ("nashua" in claim_lower or "northeast research farm" in claim_lower):
        return geo_inference(
            {"level": "farm", "name": "ISU Northeast Research Farm, Nashua, Iowa"},
            "explicit_claim",
            "claim_text",
            evidence_excerpt(claim_text, "research farm"),
        )

    if matches_any_pattern(claim_lower, BROAD_COUNTRY_PATTERNS):
        return geo_inference(
            {"level": "country", "name": "United States"},
            "explicit_claim",
            "claim_text",
            evidence_excerpt(claim_text, "United States"),
        )

    region_signal = region_scope_signal(claim_text, "claim_text")
    if region_signal:
        return region_signal

    claim_state = geo_scope_signal_from_text(claim_text, "claim_text", default_region)
    if claim_state:
        return claim_state

    if default_region_is_evidenced(claim_text, default_region):
        return geo_inference(
            normalize_region(default_region),
            "explicit_claim",
            "claim_text",
            evidence_excerpt(claim_text, default_region.get("name", "")),
        )

    if regional_shorthand_needs_run_context(claim_lower) and default_region.get("level") == "state":
        return geo_inference(
            normalize_region(default_region),
            "run_context",
            "claim_text",
            evidence_excerpt(claim_text, "this region"),
        )

    if title_mentions_default_region(title_lower, default_region):
        return geo_inference(
            normalize_region(default_region),
            "source_title_context",
            "source_title",
            evidence_excerpt(source_title, default_region.get("name", "")),
        )

    return geo_inference({"level": "global", "name": "global"}, "default_global", "", "")


def infer_source_geo_scope(
    capture: Dict[str, Any],
    default_region: Dict[str, str],
) -> Dict[str, Any]:
    source_fields = (
        ("source_title", capture.get("source_title") or capture.get("search_title") or ""),
        ("source_url", " ".join([capture.get("source_url", ""), capture.get("final_url", "")])),
        ("raw_snippet", capture.get("snippet", "") or capture.get("search_snippet", "")),
        ("source_domain", capture.get("source_domain", "")),
    )
    for field_name, field_value in source_fields:
        if not field_value:
            continue
        region_signal = region_scope_signal(field_value, field_name)
        if region_signal:
            region_signal["source"] = field_name
            return region_signal
        state_signal = geo_scope_signal_from_text(field_value, field_name, default_region)
        if state_signal:
            state_signal["source"] = field_name
            return state_signal
        if matches_any_pattern(field_value.lower(), BROAD_COUNTRY_PATTERNS):
            return geo_inference(
                {"level": "country", "name": "United States"},
                field_name,
                field_name,
                evidence_excerpt(field_value, "United States"),
            )

    return geo_inference({"level": "global", "name": "global"}, "default_global", "", "")


def build_geo_evidence(claim_geo: Dict[str, Any], source_geo: Dict[str, Any]) -> Dict[str, Any]:
    matched_locations = []
    for kind, inference in (("claim_location", claim_geo), ("source_location", source_geo)):
        scope = inference["scope"]
        if scope["level"] == "global":
            continue
        matched_locations.append(
            {
                "field": "{0}:{1}".format(kind, inference.get("field", "")),
                "level": scope["level"],
                "name": scope["name"],
                "evidence_text": inference.get("evidence_text", ""),
            }
        )
    return {
        "claim_location_source": claim_geo.get("source", ""),
        "claim_location_confidence": claim_geo.get("confidence", "none"),
        "claim_location_text": claim_geo.get("evidence_text", ""),
        "source_location_source": source_geo.get("source", ""),
        "source_location_confidence": source_geo.get("confidence", "none"),
        "source_location_text": source_geo.get("evidence_text", ""),
        "matched_locations": matched_locations,
    }


def geo_inference(
    scope: Dict[str, str],
    source: str,
    field: str,
    evidence_text: str,
) -> Dict[str, Any]:
    return {
        "scope": enrich_scope(scope),
        "source": source,
        "field": field,
        "evidence_text": evidence_text,
        "confidence": inference_confidence(source, scope),
    }


def inference_confidence(source: str, scope: Dict[str, str]) -> str:
    if scope.get("level") == "global" or source == "default_global":
        return "none"
    if source in {"explicit_claim", "explicit_state", "explicit_county", "explicit_region"}:
        return "high"
    if source in {"source_title", "raw_snippet", "source_title_context", "source_url"}:
        return "medium"
    if source in {"run_context", "source_domain"}:
        return "low"
    return "medium"


def region_scope_signal(value: str, field: str) -> Optional[Dict[str, Any]]:
    scope = production_region_scope_from_text(value)
    if scope:
        return geo_inference(scope, "explicit_region", field, evidence_excerpt(value, scope["name"]))
    return None


def geo_scope_signal_from_text(
    value: str,
    field: str,
    default_region: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    default_state_name = ""
    if default_region and default_region.get("level") == "state":
        default_state_name = default_region.get("name", "")
    county_scope = county_scope_from_text(value, default_state_name)
    if county_scope:
        return geo_inference(
            county_scope,
            "explicit_county" if field == "claim_text" else field,
            field,
            evidence_excerpt(value, county_scope["name"].split(",")[0]),
        )

    state_name = state_scope_from_text(value)
    if state_name:
        return geo_inference(
            {"level": "state", "name": state_name},
            "explicit_state" if field == "claim_text" else field,
            field,
            evidence_excerpt(value, state_name),
        )
    return None


def matches_any_pattern(value: str, patterns: Tuple[str, ...]) -> bool:
    return any(re.search(pattern, value) for pattern in patterns)


def state_scope_from_text(value: str) -> str:
    lowered = value.lower()
    for state_name, aliases in US_STATE_ALIASES.items():
        for alias in aliases:
            if alias.isupper():
                if re.search(r"\b{0}\b".format(re.escape(alias)), value):
                    return state_name
            elif re.search(r"(?<![a-z]){0}(?![a-z])".format(re.escape(alias)), lowered):
                return state_name
    return ""


def evidence_excerpt(value: str, needle: str, radius: int = 120) -> str:
    if not value:
        return ""
    if not needle:
        return value[: radius * 2].strip()
    match = re.search(re.escape(needle), value, flags=re.IGNORECASE)
    if not match:
        return value[: radius * 2].strip()
    start = max(0, match.start() - radius)
    end = min(len(value), match.end() + radius)
    return value[start:end].strip()


def default_region_is_evidenced(
    claim_text: str,
    default_region: Dict[str, str],
) -> bool:
    region_name = default_region.get("name", "")
    if not region_name:
        return False
    if state_scope_from_text(claim_text) == region_name:
        return True
    return region_name.lower() in claim_text.lower()


def regional_shorthand_needs_run_context(claim_lower: str) -> bool:
    return any(
        phrase in claim_lower
        for phrase in (
            "across the state",
            "this region",
            "northcentral and northeast regions",
            "northcentral",
            "northeast regions",
        )
    )


def title_mentions_default_region(title_lower: str, default_region: Dict[str, str]) -> bool:
    region_name = default_region.get("name", "").lower()
    if not region_name:
        return False
    institution_phrase = "{0} state university".format(region_name)
    return region_name in title_lower and institution_phrase not in title_lower


def normalize_region(region_scope: Dict[str, str]) -> Dict[str, str]:
    level = region_scope.get("level", "global")
    name = region_scope.get("name", "global")
    return {
        "level": level,
        "name": name,
    }


def infer_time_scope(attribute: str, claim_text: str) -> Dict[str, Any]:
    if attribute == "planting_window":
        return {"label": "planting season"}
    if attribute == "soil_temperature_threshold":
        return {"label": "emergence"}
    if attribute == "water_table_depth":
        return {"label": "preseason soil profile"}
    if re.search(r"\b(2017|2018|2019|2020|2021|2022|2023|2024)\b", claim_text):
        return {"label": "observed study period"}
    return {"label": "growing season"}


def parse_publication_date(publication_date_hint: str) -> str:
    cleaned = publication_date_hint.strip()
    if not cleaned:
        return ""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    iso_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", cleaned)
    if iso_match:
        return iso_match.group(0)
    return ""


def parse_publication_year(publication_date_hint: str) -> Optional[int]:
    match = re.search(r"\b(19|20)\d{2}\b", publication_date_hint)
    return int(match.group(0)) if match else None


def normalized_value_signature(claim: Dict[str, Any]) -> Tuple[str, Any]:
    value = claim["value"]
    if value["value_type"] == "numeric":
        numeric = value.get("normalized_numeric_value", value.get("numeric_value"))
        return "numeric", round(float(numeric), 2)
    if value["value_type"] == "range":
        range_min = value.get("normalized_range_min", value.get("range_min"))
        range_max = value.get("normalized_range_max", value.get("range_max"))
        return "range", (round(float(range_min), 2), round(float(range_max), 2))
    return "text", value.get("text_value", value.get("raw_value_text"))


def value_from_extraction(extraction: Dict[str, Any]) -> Dict[str, Any]:
    evidence = extraction.get("evidence_text", "")
    qualifier = extraction.get("qualifier") or "descriptive"
    value_type = extraction.get("value_type")
    if value_type == "numeric" and extraction.get("numeric_value") is not None:
        value = {
            "value_type": "numeric",
            "raw_value_text": evidence,
            "numeric_value": float(extraction["numeric_value"]),
            "qualifier": qualifier,
        }
        if extraction.get("unit"):
            value["unit"] = extraction["unit"]
        return value
    if (
        value_type == "range"
        and extraction.get("range_min") is not None
        and extraction.get("range_max") is not None
    ):
        value = {
            "value_type": "range",
            "raw_value_text": evidence,
            "range_min": float(extraction["range_min"]),
            "range_max": float(extraction["range_max"]),
            "qualifier": qualifier,
        }
        if extraction.get("unit"):
            value["unit"] = extraction["unit"]
        return value
    return {
        "value_type": "text",
        "raw_value_text": evidence,
        "text_value": extraction.get("claim_summary") or evidence,
        "qualifier": qualifier,
    }


def observation_type_from_qualifier(qualifier: str) -> str:
    return {
        "risk": "risk",
        "recommended": "recommendation",
        "threshold": "threshold",
        "optimal": "condition",
        "descriptive": "condition",
    }.get(qualifier, "condition")


def applicability_key(claim: Dict[str, Any]) -> Tuple[Any, ...]:
    agronomic = claim.get("agronomic_scope") or {}
    return (
        claim["entity"]["name"],
        claim["parameter_id"],
        claim["location_scope"]["level"],
        claim["location_scope"]["name"],
        claim["time_scope"]["label"],
        agronomic.get("cultivar"),
        agronomic.get("management_system"),
    )


def merge_extraction_claims(claims: List[Dict[str, Any]], run_id: str) -> List[Dict[str, Any]]:
    """Dedup/merge within the combined applicability key, then assign claim ids."""
    grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    order: List[Tuple[Any, ...]] = []
    for claim in claims:
        key = applicability_key(claim)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(claim)

    merged: List[Dict[str, Any]] = []
    for key in order:
        merged.extend(_merge_applicability_group(grouped[key]))

    for index, claim in enumerate(merged, start=1):
        claim["claim_id"] = "{0}-llm-claim-{1:04d}".format(run_id, index)
    return merged


def _merge_applicability_group(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    text_claims: List[Dict[str, Any]] = []
    text_index: Dict[str, Dict[str, Any]] = {}
    numeric_claims: List[Dict[str, Any]] = []
    for claim in group:
        if claim["value"]["value_type"] in {"numeric", "range"}:
            numeric_claims.append(claim)
            continue
        existing = text_index.get(claim["claim_text"])
        if existing is not None:
            _union_sources(existing, claim)
        else:
            text_index[claim["claim_text"]] = claim
            text_claims.append(claim)

    merged_numeric: List[Dict[str, Any]] = []
    for claim in numeric_claims:
        placed = False
        for target in merged_numeric:
            if quantitative_values_compatible(target["value"], claim["value"]):
                _merge_quantitative_values(target, claim)
                _union_sources(target, claim)
                placed = True
                break
        if not placed:
            merged_numeric.append(claim)
    return text_claims + merged_numeric


def _union_sources(target: Dict[str, Any], other: Dict[str, Any]) -> None:
    urls = target["provenance"]["source_urls"]
    for url in other["provenance"]["source_urls"]:
        if url not in urls:
            urls.append(url)


def _merge_quantitative_values(target: Dict[str, Any], other: Dict[str, Any]) -> None:
    low1, high1 = value_interval(target["value"])
    low2, high2 = value_interval(other["value"])
    low, high = min(low1, low2), max(high1, high2)
    value = target["value"]
    for stale in ("numeric_value", "range_min", "range_max"):
        value.pop(stale, None)
    if low == high:
        value["value_type"] = "numeric"
        value["numeric_value"] = low
    else:
        value["value_type"] = "range"
        value["range_min"] = low
        value["range_max"] = high


def apply_conflict_flags(claims: List[Dict[str, Any]]) -> Dict[str, int]:
    grouped: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = {}
    for claim in claims:
        if claim["value"]["value_type"] not in {"numeric", "range"}:
            continue
        grouped.setdefault(conflict_group_key(claim), []).append(claim)

    conflict_groups = 0
    conflict_claims = 0
    for group_claims in grouped.values():
        domains = {claim["provenance"]["source_domain"] for claim in group_claims}
        if len(domains) > 1 and group_has_incompatible_values(group_claims):
            conflict_groups += 1
            for claim in group_claims:
                claim["conflict_status"] = "potential"
                claim["conflict_reason"] = (
                    "multiple incompatible quantitative claims for the same attribute subtype and scope"
                )
                conflict_claims += 1
    return {"groups": conflict_groups, "claims": conflict_claims}


def conflict_group_key(claim: Dict[str, Any]) -> Tuple[Any, ...]:
    agronomic = claim.get("agronomic_scope") or {}
    return (
        claim["entity"]["name"],
        claim.get("attribute_subtype", claim["attribute"]),
        claim["location_scope"]["level"],
        claim["location_scope"]["name"],
        claim["time_scope"]["label"],
        agronomic.get("cultivar"),
        agronomic.get("management_system"),
    )


def group_has_incompatible_values(claims: List[Dict[str, Any]]) -> bool:
    for index, left in enumerate(claims):
        for right in claims[index + 1 :]:
            if not quantitative_values_compatible(left["value"], right["value"]):
                return True
    return False


def quantitative_values_compatible(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_min, left_max = value_interval(left)
    right_min, right_max = value_interval(right)
    tolerance = 0.5
    return max(left_min, right_min) <= min(left_max, right_max) + tolerance


def value_interval(value: Dict[str, Any]) -> Tuple[float, float]:
    if value["value_type"] == "range":
        range_min = value.get("normalized_range_min", value.get("range_min"))
        range_max = value.get("normalized_range_max", value.get("range_max"))
        return float(range_min), float(range_max)
    numeric = value.get("normalized_numeric_value", value.get("numeric_value"))
    return float(numeric), float(numeric)


def capture_now() -> str:
    from datetime import datetime as utc_datetime, timezone

    return utc_datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
