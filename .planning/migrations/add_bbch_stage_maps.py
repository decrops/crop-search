#!/usr/bin/env python3
"""Phase 1: add bbch_stage_map to the 7 crop profiles.

Maps each crop's existing growth_stage_terms to BBCH principal-stage ranges.
Mappings are standard BBCH alignments; confidence is per-entry, and non-BBCH
operational terms (e.g. tomato "transplanting") are marked low with a note.
Idempotent: overwrites bbch_stage_map each run. Only terms present in a crop's
growth_stage_terms are emitted, so it stays in sync with the profile.

Run:  .venv/bin/python .planning/migrations/add_bbch_stage_maps.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CROPS = REPO / "config" / "crops"

# crop_id -> { local_term: (bbch_min, bbch_max, confidence, source_note) }
MAPS = {
    "wheat": {
        "germination": (0, 9, "high", ""),
        "emergence": (9, 11, "high", ""),
        "tillering": (20, 29, "high", ""),
        "jointing": (30, 39, "high", "stem elongation"),
        "heading": (50, 59, "high", ""),
        "flowering": (60, 69, "high", "anthesis"),
        "grain fill": (70, 79, "high", ""),
        "maturity": (80, 99, "medium", "ripening through senescence"),
    },
    "corn": {
        "germination": (0, 9, "high", ""),
        "emergence": (9, 11, "high", ""),
        "vegetative": (12, 39, "medium", "leaf development through stem elongation"),
        "tasseling": (51, 59, "high", "VT"),
        "silking": (61, 69, "high", "R1 flowering"),
        "grain fill": (70, 79, "high", "R2-R5"),
        "maturity": (80, 99, "medium", "R6 physiological maturity onward"),
    },
    "rice": {
        "germination": (0, 9, "high", ""),
        "seedling": (10, 19, "high", ""),
        "tillering": (20, 29, "high", ""),
        "panicle initiation": (30, 39, "medium", "stem elongation / panicle development"),
        "heading": (50, 59, "high", ""),
        "flowering": (60, 69, "high", "anthesis"),
        "grain fill": (70, 79, "high", ""),
        "maturity": (80, 99, "medium", ""),
    },
    "soybean": {
        "germination": (0, 9, "high", ""),
        "emergence": (9, 11, "high", "VE"),
        "vegetative": (12, 29, "medium", "Vn stages"),
        "flowering": (60, 69, "high", "R1-R2"),
        "pod development": (70, 75, "high", "R3-R4"),
        "seed fill": (75, 79, "high", "R5-R6"),
        "maturity": (80, 99, "medium", "R7-R8"),
    },
    "cotton": {
        "germination": (0, 9, "high", ""),
        "emergence": (9, 11, "high", ""),
        "squaring": (51, 59, "high", "flower-bud (square) formation"),
        "flowering": (60, 69, "high", ""),
        "boll development": (70, 79, "high", ""),
        "boll opening": (80, 89, "high", ""),
        "maturity": (90, 99, "medium", ""),
    },
    "sunflower": {
        "germination": (0, 9, "high", ""),
        "emergence": (9, 11, "high", ""),
        "vegetative": (12, 39, "medium", "leaf development through stem elongation"),
        "bud": (51, 59, "high", "inflorescence (star/bud) emergence"),
        "flowering": (60, 69, "high", ""),
        "seed fill": (70, 79, "high", ""),
        "physiological maturity": (80, 89, "medium", ""),
    },
    "tomato": {
        "germination": (0, 9, "high", ""),
        "seedling": (10, 19, "high", ""),
        "transplanting": (12, 15, "low", "operational timing, not a BBCH principal stage"),
        "vegetative": (12, 39, "medium", ""),
        "flowering": (60, 69, "high", ""),
        "fruit set": (70, 71, "medium", ""),
        "fruit development": (71, 79, "high", ""),
        "maturity": (80, 89, "medium", "ripening"),
    },
}


def main():
    for crop_id, mapping in MAPS.items():
        path = CROPS / f"{crop_id}.json"
        profile = json.loads(path.read_text())
        terms = profile.get("growth_stage_terms", [])
        unknown = set(mapping) - set(terms)
        if unknown:
            raise SystemExit(f"{crop_id}: map terms not in growth_stage_terms: {sorted(unknown)}")
        rows = []
        for term in terms:
            if term not in mapping:
                raise SystemExit(f"{crop_id}: growth_stage_term '{term}' has no BBCH mapping")
            lo, hi, conf, note = mapping[term]
            row = {"local_term": term, "bbch_min": lo, "bbch_max": hi, "confidence": conf}
            if note:
                row["source_note"] = note
            rows.append(row)
        profile["bbch_stage_map"] = rows
        path.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"{crop_id}: {len(rows)} stage mappings")


if __name__ == "__main__":
    main()
