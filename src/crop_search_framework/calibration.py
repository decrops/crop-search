"""Phase 3 — local-calibration vault note.

Surfaces the genuinely-local gaps (lime/fertilizer rates → soil test; exact
sowing date → season) that no global corpus can close, as a bounded checklist
linking the relevant data-point notes. Marks the gap as *known* rather than
silently absent. Optionally flags the unit-mixed parameters from the
unit-normalization report so the reader treats those numbers with care.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional


def _value_summaries(repo_root: Path, run_id: str, claims_subdir: str) -> Dict[str, str]:
    """Map parameter_id -> a compact value summary from normalized claims."""
    src = repo_root / "exploration" / claims_subdir / run_id
    out: Dict[str, List[str]] = {}
    for f in sorted(src.glob("*.json")):
        if f.name == "summary.json":
            continue
        c = json.loads(f.read_text(encoding="utf-8"))
        v = c["value"]
        if v["value_type"] == "numeric" and v.get("numeric_value") is not None:
            s = "{0} {1}".format(v["numeric_value"], v.get("unit", "")).strip()
        elif v["value_type"] == "range":
            s = "{0}–{1} {2}".format(v.get("range_min"), v.get("range_max"), v.get("unit", "")).strip()
        else:
            continue
        out.setdefault(c["parameter_id"], []).append(s)
    return {pid: ", ".join(sorted(set(vals))[:4]) for pid, vals in out.items()}


def build_calibration_note(
    repo_root: Path,
    run_id: str,
    crop: str,
    region: str,
    claims_subdir: str = "normalized",
    generated_at: Optional[str] = None,
) -> str:
    generated_at = generated_at or date.today().isoformat()
    vals = _value_summaries(repo_root, run_id, claims_subdir)
    flags: List[Dict[str, Any]] = []
    units_report = repo_root / "exploration" / "normalized_units" / run_id / "units_report.json"
    if units_report.exists():
        flags = json.loads(units_report.read_text(encoding="utf-8")).get("flags", [])

    def vlink(pid: str, label: str) -> str:
        summary = vals.get(pid, "—")
        note = "[[{0} — {1}]]".format(crop.capitalize(), label)
        return "- {0}: vault range **{1}** → confirm locally. {2}".format(label, summary, note)

    crop_title = crop.capitalize()
    lines = [
        "---",
        'title: "{0} — Local calibration ({1})"'.format(crop_title, region),
        "type: calibration",
        "crop: {0}".format(crop),
        "region: {0}".format(region),
        "run_id: {0}".format(run_id),
        "generated_by: crop-search",
        "generated_at: {0}".format(generated_at),
        "tags:",
        "  - crop/{0}".format(crop),
        "  - kind/local-calibration",
        "---",
        "",
        "# {0} — Local calibration ({1})".format(crop_title, region),
        "",
        "> The vault values are global consensus ranges. The items below depend on **your soil test** "
        "or **the running season** and cannot be set from the corpus — calibrate them locally before drilling.",
        "",
        "## From a soil test (do not use vault numbers directly)",
        vlink("soil.ph_range", "Soil pH range") + "  → lime to target pH if below the low end.",
        vlink("nutrients.nitrogen_requirement", "Nitrogen requirement") + "  → adjust to yield goal + soil N credits.",
        vlink("nutrients.phosphorus_requirement", "Phosphorus requirement") + "  → apply to soil-test P.",
        vlink("nutrients.potassium_requirement", "Potassium requirement") + "  → apply to soil-test K.",
        vlink("soil.salinity_threshold", "Soil salinity threshold") + "  → usually a non-issue in humid regions.",
        "",
        "## From the running season",
        vlink("planting.planting_window", "Planting window") + "  → pick the exact sowing date within the window for the year.",
        vlink("planting.seeding_rate", "Seeding rate") + "  → raise toward the high end for late sowing.",
        vlink("water.irrigation_trigger", "Irrigation trigger") + "  → only if a dry spell hits the sensitive stages.",
        "",
    ]
    if flags:
        lines += ["## Unit-mixed parameters (treat vault range with care)", ""]
        seen = set()
        for fl in flags:
            pid = fl["parameter_id"]
            if pid in seen:
                continue
            seen.add(pid)
            lines.append("- `{0}`: {1}".format(pid, fl["message"]))
        lines.append("")
    lines += ["## Known gaps not in the corpus", "",
              "- Variety selection — choose a regionally-listed {0} cultivar.".format(crop),
              "- Disease/pest/weed program — see the crop-protection notes once the protection run completes.",
              ""]
    return "\n".join(lines)


def render_local_calibration(
    repo_root: Path,
    run_id: str,
    crop: str,
    region: str,
    vault_path: Path,
    subdir: str = "DeCropsResearch/crop_science",
    claims_subdir: str = "normalized",
    dry_run: bool = True,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    content = build_calibration_note(repo_root, run_id, crop, region, claims_subdir, generated_at)
    target_dir = vault_path / subdir
    filename = "{0} — Local calibration ({1}).md".format(crop.capitalize(), region)
    target = target_dir / filename
    result = {"run_id": run_id, "file": str(target), "dry_run": dry_run, "bytes": len(content)}
    if dry_run:
        result["preview"] = content
        return result
    # Marker-guarded write: only create or overwrite our own generated note.
    if target.exists() and "generated_by: crop-search" not in target.read_text(encoding="utf-8"):
        result["skipped_foreign"] = True
        return result
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result["written"] = True
    return result
