#!/usr/bin/env python3
"""Phase 1 migration: parameter manifest 0.2.0 -> 0.3.0.

Adds the v2 ontology fields to every existing parameter (deriving them from
existing data so nothing is fabricated) and appends `implementation_status: stub`
placeholders so all 12 decision domains are visible end-to-end. Idempotent:
re-running recomputes the derived fields and de-duplicates stubs by parameter_id.

Run:  PYTHONPATH=src .venv/bin/python .planning/migrations/migrate_manifest_v030.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "config" / "parameters" / "core-crop-parameters.json"

ALL_GROUPS = {"cereal", "legume", "oilseed", "fiber", "tuber", "forage", "vegetable"}

# family -> decision domain. Per-id overrides applied afterwards.
FAMILY_DOMAIN = {
    "temperature": "climate_site",
    "thermal_time": "growth_monitoring",
    "phenology": "growth_monitoring",
    "canopy": "growth_monitoring",
    "photosynthesis": "growth_monitoring",
    "root": "growth_monitoring",
    "morphology": "growth_monitoring",
    "water": "water_management",
    "soil": "soil_requirements",
    "nutrients": "nutrient_management",
    "stress": "stress_abiotic",
    "planting": "planting_establishment",
    "quality": "post_harvest_quality",
    "harvest": "harvest",
    "management": "soil_prep_tillage",
}

# temperature thresholds that are really abiotic-stress concepts go to stress_abiotic.
TEMP_STRESS = {
    "temperature.heat_stress_threshold",
    "temperature.survival_temperature",
    "temperature.reproductive_heat_threshold",
    "temperature.grain_fill_temperature",
}

# Curated set of genuinely stage-varying concepts (drives {stage} query expansion).
# Kept tight on purpose so stage expansion does not explode the query plan.
STAGE_DEPENDENT = {
    "water.crop_coefficient",
    "water.drought_sensitive_stage",
    "nutrients.nitrogen_timing",
    "stress.heat_sensitive_stage",
    "stress.frost_risk_stage",
    "temperature.grain_fill_temperature",
    "temperature.reproductive_heat_threshold",
}


def domain_for(p):
    if p["parameter_id"] in TEMP_STRESS:
        return "stress_abiotic"
    return FAMILY_DOMAIN[p["family"]]


def kind_for(p):
    # operation = a recommended action; everything else is a measured/observed trait.
    return "operation" if p["category"] == "management_recommendation" else "trait"


def concept_scope_for(p):
    groups = set(p.get("applies_to_crop_groups", []))
    return "universal" if (not groups or ALL_GROUPS.issubset(groups)) else "crop_group"


def decision_for(p):
    mr = p.get("management_relevance") or []
    if mr:
        return "Informs " + ", ".join(mr[:2])
    return "Informs {0} decisions".format(p["family"].replace("_", " "))


STUBS = [
    ("variety_cultivar.maturity_class", "Maturity class", "variety", "phenology_parameter",
     "universal", "trait", "Informs variety selection",
     ["maturity class", "maturity group", "days to maturity class"]),
    ("variety_cultivar.disease_resistance_package", "Disease resistance package", "variety",
     "quality_parameter", "crop_group", "trait", "Informs variety selection, disease management",
     ["disease resistance package", "resistance rating", "tolerance package"]),
    ("crop_protection.key_disease_pressure", "Key disease pressure and timing", "crop_protection",
     "management_recommendation", "crop_group", "operation",
     "Informs fungicide timing, disease management",
     ["key disease pressure", "disease threshold", "fungicide timing"], True),
    ("crop_protection.key_pest_threshold", "Key insect pest action threshold", "crop_protection",
     "management_recommendation", "crop_group", "operation",
     "Informs insecticide timing, pest management",
     ["insect action threshold", "pest economic threshold", "scouting threshold"], True),
    ("crop_protection.weed_management", "Weed management program", "crop_protection",
     "management_recommendation", "universal", "operation",
     "Informs herbicide program, weed management",
     ["weed management", "herbicide program", "critical weed-free period"]),
    ("post_harvest_quality.storage_conditions", "Storage moisture and temperature", "post_harvest",
     "management_recommendation", "universal", "operation",
     "Informs drying and storage management",
     ["storage moisture", "storage temperature", "safe storage conditions"]),
    ("economics.input_intensity", "Typical input intensity", "economics",
     "management_recommendation", "crop_group", "trait",
     "Informs budgeting and rotation fit",
     ["input intensity", "typical input cost", "production budget"]),
]


def build_stub(entry):
    pid, label, family, category, scope, kind, decision, aliases = entry[:8]
    requires_stage = entry[8] if len(entry) > 8 else False
    subtype = pid.split(".", 1)[1]
    return {
        "parameter_id": pid,
        "label": label,
        "family": family,
        "category": category,
        "value_type": "text",
        "canonical_units": [],
        "applies_to_crop_groups": sorted(ALL_GROUPS) if scope == "universal" else [],
        "normalized_attribute": family,
        "normalized_attribute_subtype": subtype,
        "search_aliases": aliases,
        "evidence_patterns": ["{crop} " + aliases[0]],
        "management_relevance": [],
        "required_scope": ["crop", "region"],
        "review_policy": {
            "allow_canonical": False,
            "conflict_key": ["crop", "parameter_id", "scope"],
            "merge_if_values_overlap": False,
        },
        "domain": pid.split(".", 1)[0],
        "parameter_kind": kind,
        "concept_scope": scope,
        "decision": decision,
        "requires_stage_context": bool(requires_stage),
        "implementation_status": "stub",
    }


def main():
    data = json.loads(MANIFEST.read_text())
    params = [p for p in data["parameters"] if p.get("implementation_status") != "stub"]

    for p in params:
        p["domain"] = domain_for(p)
        p["parameter_kind"] = kind_for(p)
        p["concept_scope"] = concept_scope_for(p)
        p["decision"] = decision_for(p)
        p["requires_stage_context"] = p["parameter_id"] in STAGE_DEPENDENT
        p["implementation_status"] = "active"

    stubs = [build_stub(e) for e in STUBS]
    data["parameters"] = params + stubs
    data["manifest_version"] = "0.3.0"
    data["scope"] = "major crop physiology, management, and farmer decision domains"

    MANIFEST.write_text(json.dumps(data, indent=2) + "\n")
    active = sum(1 for p in data["parameters"] if p["implementation_status"] == "active")
    stub = sum(1 for p in data["parameters"] if p["implementation_status"] == "stub")
    print(f"wrote {MANIFEST.relative_to(REPO)}: {active} active + {stub} stub = {len(data['parameters'])}")
    import collections
    dom = collections.Counter(p["domain"] for p in data["parameters"])
    for d, c in sorted(dom.items()):
        print(f"  {c:3d}  {d}")


if __name__ == "__main__":
    main()
