# Normalized extraction contract (Phase 1 design)

This is the contract the Phase 2 Claude extractor + `normalize.py` / `review.py` / `promote.py` must honor. Schema support already landed (additive, optional) in Phase 1:
- `schemas/normalized-claim.schema.json` — `agronomic_scope`, `bbch_applicability`, `provenance.manifest_version`
- `schemas/parameter-manifest.schema.json` — `domain`, `parameter_kind`, `concept_scope`, `decision`, `requires_stage_context`, `implementation_status`

## What the extractor emits per claim
For each attributable fact found in a source, the extractor returns:
- `parameter_id` — constrained to the **active** manifest enum (stubs/deferred are not valid targets) or `none` (dropped).
- `value` — typed `{ value_type, numeric/range/text, unit, qualifier }` (existing shape).
- `evidence_text` — verbatim span supporting the value (already required in `provenance`).
- `agronomic_scope` — `{ cultivar?, management_system? }` **only when stated**; omit when general.
- `bbch_applicability` — `{ bbch_min, bbch_max, confidence, evidence_text }` **only when the value is tied to a stage in the evidence**; omit otherwise.
- crop / region / season continue to populate the existing `entity` / `location_scope` / `time_scope`.
- `provenance.manifest_version` — the manifest version the extraction targeted.

## Derived, NOT stored
`domain` and `parameter_kind` are functions of `parameter_id`. They are **not** written onto claims. `review.py` / export materialize them by looking up `parameter_id` in the manifest identified by `provenance.manifest_version`. This avoids stale durable records when the manifest evolves.

## The combined applicability key (single source of truth)
Conflict detection, dedup, and merge operate on:

```
applicability_key = (
    entity.name,                 # crop
    parameter_id,
    location_scope.level, location_scope.name,   # region
    time_scope.label,            # season
    agronomic_scope.cultivar, agronomic_scope.management_system,
)
```

There is no separate `claim_applicability` object. Two values that differ only in `management_system` (e.g. irrigated vs dryland N rate) are **not** a conflict — they are distinct applicability keys.

## Dedup / merge
1. **Exact-text dedup** within an `applicability_key`: identical `claim_text` collapses to one claim with a `provenance.source_urls[]` list. (Targets the current 45.5% redundancy / 30×-repeat pathology.)
2. **Value merge** for numeric/range within an `applicability_key`: overlapping intervals merge into one record with N sources, reusing `quantitative_values_compatible()`.

## Review / promotion semantics
- Conflicts computed within `applicability_key` (see above).
- Promotion gates: require completeness of the applicability key appropriate to the parameter's `required_scope`, honor `parameter_kind` (operations may require a stage or timing), use `bbch_applicability` evidence where `requires_stage_context` is true, and reject `descriptive` / low-confidence claims (the current promotion bottleneck).
- `bbch_applicability.bbch_min/max` should fall within the crop profile's `bbch_stage_map` ranges; out-of-range stages lower confidence.

## Backward compatibility
All claim fields above are optional. Pre-v2 artifacts remain valid; no migration. New runs stamp `provenance.manifest_version`; old runs without it materialize against the manifest version recorded in their run summary, or are reported as `unversioned`.
