"""Phase 3 — unit-normalization cleanup for the unit-mixed parameters.

Two operations, both conservative:
1. **Safe conversion** — °F → °C is always valid, so any claim whose unit is
   Fahrenheit is converted in place (value + unit), keeping ``raw_value_text``.
2. **Flag-only** — ambiguous unit mixing (seeds/m² recorded as kg/ha, rooting
   depth in cm recorded as m, plant density per-m² vs per-ha) is **not**
   auto-converted (too risky); instead it's recorded in a report so the
   local-calibration note and downstream consumers know which numbers are
   unreliable. Converting these requires per-source context (e.g. TKW) we don't
   have, so flagging beats guessing.

Reads `exploration/normalized/<run>/`, writes cleaned claims to
`exploration/<output_subdir>/<run>/` plus `units_report.json`. Cleaned claims
stay schema-valid (no new fields on the claim; flags live only in the report).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schema_registry import SchemaRegistry

FAHRENHEIT_UNITS = {"°f", "f", "fahrenheit", "deg f", "degrees f", "degree f"}

# param_id -> (unit_substring, max_plausible, message). If a numeric/range value
# exceeds max_plausible under that unit, flag it as likely unit-mixed.
OUTLIER_RULES: Dict[str, Tuple[str, float, str]] = {
    "planting.seeding_rate": ("kg/ha", 600.0, "value >600 kg/ha is likely seeds/m² or a seed count mis-unit"),
    "planting.target_plant_density": ("", 100000.0, "value >100000 mixes plants/m² with plants/ha"),
    "root.maximum_rooting_depth": ("m", 5.0, "rooting depth >5 m is almost certainly cm recorded as m"),
    "morphology.spike_density": ("", 5000.0, "spike density mixes ears/plant with ears/m²"),
    "canopy.harvest_index": ("", 1.5, "harvest index mixes fraction (0–1) with percent"),
    "soil.soil_water_holding_capacity": ("", 100.0, "value >100 mixes % with mm or mm/m TAW"),
    "water.evapotranspiration_requirement": ("mm", 2000.0, "ET >2000 mm mixes daily mm with seasonal mm"),
    "water.allowable_depletion": ("", 1.0, "depletion mixes fraction (0–1) with percent"),
}


def _to_celsius(f: float) -> float:
    return round((f - 32.0) * 5.0 / 9.0, 2)


def _is_fahrenheit(unit: Optional[str]) -> bool:
    return (unit or "").strip().lower() in FAHRENHEIT_UNITS


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def canonicalize_value(parameter_id: str, value: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return (possibly-converted value, list of report entries)."""
    notes: List[Dict[str, Any]] = []
    value = dict(value)

    # 1) Safe °F -> °C conversion.
    if _is_fahrenheit(value.get("unit")):
        before = dict(value)
        for key in ("numeric_value", "range_min", "range_max"):
            x = _as_float(value.get(key))
            if x is not None:
                value[key] = _to_celsius(x)
        value["unit"] = "celsius"
        notes.append({
            "parameter_id": parameter_id, "kind": "converted", "from_unit": before.get("unit"),
            "to_unit": "celsius",
            "before": {k: before.get(k) for k in ("numeric_value", "range_min", "range_max")},
            "after": {k: value.get(k) for k in ("numeric_value", "range_min", "range_max")},
        })

    # 2) Flag-only outliers.
    rule = OUTLIER_RULES.get(parameter_id)
    if rule:
        unit_sub, max_plausible, message = rule
        unit = (value.get("unit") or "").lower()
        if not unit_sub or unit_sub in unit:
            mx = max(
                (abs(x) for x in (_as_float(value.get("numeric_value")),
                                  _as_float(value.get("range_min")), _as_float(value.get("range_max")))
                 if x is not None),
                default=None,
            )
            if mx is not None and mx > max_plausible:
                notes.append({"parameter_id": parameter_id, "kind": "flag",
                              "unit": value.get("unit"), "value": mx, "message": message})
    return value, notes


def normalize_units_run(
    repo_root: Path,
    run_id: str,
    claims_subdir: str = "normalized",
    output_subdir: str = "normalized_units",
) -> Dict[str, Any]:
    registry = SchemaRegistry(repo_root)
    src = repo_root / "exploration" / claims_subdir / run_id
    if not src.exists():
        raise FileNotFoundError("no claims at {0}".format(src))
    out = repo_root / "exploration" / output_subdir / run_id
    out.mkdir(parents=True, exist_ok=True)

    converted = 0
    flagged: List[Dict[str, Any]] = []
    written = 0
    for f in sorted(src.glob("*.json")):
        if f.name == "summary.json":
            continue
        claim = json.loads(f.read_text(encoding="utf-8"))
        new_value, notes = canonicalize_value(claim.get("parameter_id", ""), claim["value"])
        claim["value"] = new_value
        registry.validate("normalized-claim.schema.json", claim)
        (out / f.name).write_text(json.dumps(claim, indent=2) + "\n", encoding="utf-8")
        written += 1
        for n in notes:
            if n["kind"] == "converted":
                converted += 1
            else:
                flagged.append({**n, "claim_id": claim.get("claim_id", "")})

    report = {
        "run_id": run_id,
        "claims_written": written,
        "fahrenheit_converted": converted,
        "flagged_count": len(flagged),
        "flags": flagged,
        "output_dir": str(out.relative_to(repo_root)),
    }
    (out / "units_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k != "flags"}
    summary["flagged_params"] = sorted({f["parameter_id"] for f in flagged})
    return summary
