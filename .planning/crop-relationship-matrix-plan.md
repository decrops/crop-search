# Crop Relationship Matrix Implementation Plan

Status: Draft implementation plan
Date: 2026-06-24
Related plans:
- `.planning/generalized-crop-ontology-plan.md`
- `.planning/parameter-ontology-expansion-plan.md`
- `.planning/pipeline-quality-rework-plan.md`
- `.planning/opus-vault-extraction-plan.md`

## Goal

Add a crop-to-crop relationship layer that can answer farmer-facing questions such as:

- Which crops work well with this crop in rotation?
- Which preceding crops improve or harm this crop?
- Which crops can be intercropped, relay-cropped, strip-cropped, or companion-planted with this crop?
- Which pairs should be avoided because of disease carryover, allelopathy, pest buildup, harvest conflict, water competition, or nutrient competition?

The result should cover relationships between all crops in the configured crop universe, while preserving the current pipeline's evidence-first posture. "Covered" means every crop pair has an explicit matrix cell and status, not that every crop pair has a fabricated compatibility score.

## Core Design

Use a dense crop-pair coverage matrix backed by sparse evidence claims.

Dense matrix:

- Every configured crop appears as a matrix row and column.
- Every ordered pair exists, including self-pairs for continuous cropping and same-crop rotation interval claims.
- Each cell has a status: `not_searched`, `searched_no_evidence`, `evidence_found`, `conflicting_evidence`, `not_applicable`, or `out_of_scope`.
- Missing evidence is stored as unknown, never inferred as neutral or bad.

Sparse evidence graph:

- Only evidence-backed relationship claims are stored as claims.
- A matrix cell aggregates zero or more claims.
- Farmer-facing "likes / dislikes" views are derived from claims and cell summaries, not directly extracted as universal scores.

This gives full crop coverage without requiring an impossible all-pairs knowledge base on day one.

## Relationship Semantics

Relationships are directional unless explicitly symmetric.

Example:

```json
{
  "subject_crop": "corn",
  "object_crop": "soybean",
  "relationship_mode": "rotation",
  "relationship_subtype": "previous_crop_effect",
  "direction": "object_precedes_subject",
  "effect": "beneficial",
  "mechanisms": ["nitrogen", "disease_break"],
  "confidence": "medium"
}
```

This is not the same as soybean after corn. The matrix must support both directions.

### Relationship Modes

Initial modes:

- `rotation`: one crop before or after another across seasons.
- `continuous_cropping`: same crop following itself.
- `double_crop`: sequential crops in the same season.
- `intercrop`: two crops grown in the same field at the same time.
- `relay_crop`: overlap in time, with one crop established before the other is removed.
- `strip_crop`: simultaneous crops arranged in strips.
- `mixed_crop`: simultaneous crops without a clear row/strip structure.
- `companion_crop`: looser farmer-facing companion planting recommendations.
- `cover_crop`: cover crop before, after, or under the main crop.

### Effect Taxonomy

Extract categorical evidence first:

- `beneficial`
- `compatible`
- `conditional`
- `neutral`
- `incompatible`
- `avoid`
- `unknown`

Optional derived scores can be computed later from evidence count, source quality, recency, agreement, and context match. Scores should not be extracted directly from prose unless the source states a numeric index such as land equivalent ratio.

### Mechanism Tags

Mechanisms explain why a pair is beneficial or risky:

- `nitrogen`
- `nutrient_competition`
- `water_competition`
- `light_competition`
- `canopy_structure`
- `weed_suppression`
- `pest_suppression`
- `pest_carryover`
- `disease_break`
- `disease_carryover`
- `allelopathy`
- `residue`
- `soil_structure`
- `harvest_logistics`
- `planting_timing`
- `market_or_equipment_fit`

Mechanism tags should be optional and evidence-backed.

## Crop Universe and All-Crop Coverage

The relationship matrix should be generated from the configured crop universe:

1. Load all crop profiles from `config/crops/*.json`.
2. Use each profile's canonical crop name, aliases, scientific names, and crop group.
3. Build all ordered pairs `(subject_crop, object_crop)` across the crop universe.
4. Include self-pairs for continuous cropping, monoculture, and same-crop rotation interval evidence.
5. Add crop-group placeholder pairs when a source speaks generically, such as `corn -> legumes` or `tomato -> brassicas`.

Coverage rule:

- Every configured crop must have a matrix row.
- Every configured crop must have a matrix column.
- Every ordered pair must have a matrix cell even before it is searched.
- Unknown cells stay visible as `not_searched` or `searched_no_evidence`.
- Adding a new crop profile automatically expands the matrix.

The default operational mode can remain budgeted and prioritized, but the data model must support an exhaustive full-matrix run split into batches.

## Crop Rotation as the High-Level Matrix Implementation

The existing rotation approach should become the high-level temporal view over this relationship matrix.

Current manifest entries:

- `management.rotation_recommendation`: active coarse rotation recommendation.
- `management.rotation_interval`: stub for years or seasons between crops.
- `management.cover_crop_compatibility`: stub for cover crop fit.
- `management.double_crop_window`: stub for sequential same-season cropping.

Keep these IDs for compatibility. Do not rename or delete them.

Future behavior:

- `management.rotation_recommendation` remains a high-level summary parameter for sources that discuss rotation generally without naming a pair.
- Pair-specific evidence goes into the relationship matrix with `relationship_mode: rotation`.
- `management.rotation_interval` maps to matrix claims with `relationship_subtype: minimum_interval` or `same_crop_break_interval`.
- `management.cover_crop_compatibility` maps to matrix claims with `relationship_mode: cover_crop`.
- `management.double_crop_window` maps to matrix claims with `relationship_mode: double_crop`.

Farmer-facing rotation output is then generated from matrix cells:

- Good preceding crops.
- Good following crops.
- Crops to avoid before this crop.
- Crops to avoid after this crop.
- Minimum break interval before repeating the same crop.
- Conditional rotations by region, management system, disease pressure, or residue constraints.

This keeps crop rotation high-level and usable while allowing exact pairwise claims underneath.

## Intercropping and Companion Cropping

Intercropping is a concurrent relationship, not a single-crop parameter.

Add relationship claims for:

- Compatibility of two crops grown together.
- Spatial arrangement, such as row ratio, strip width, within-row mixture, or under-sowing.
- Temporal arrangement, such as relay timing.
- Density adjustment for one or both crops.
- Fertility, water, pest, disease, and yield effects.
- Numeric outcomes, especially land equivalent ratio and yield change.

Example claim:

```json
{
  "subject_crop": "corn",
  "object_crop": "soybean",
  "relationship_mode": "intercrop",
  "relationship_subtype": "yield_effect",
  "effect": "conditional",
  "value": {
    "value_type": "range",
    "raw_value_text": "LER 1.08-1.32",
    "range_min": 1.08,
    "range_max": 1.32,
    "unit": "land_equivalent_ratio"
  },
  "arrangement": {
    "pattern": "row_intercrop",
    "row_ratio": "2:2"
  },
  "context": {
    "region": "global",
    "management_system": "rainfed"
  }
}
```

## Proposed Manifest Concepts

Do not add these directly as active parameters. Add them first as stubs or a separate relationship vocabulary after schema support exists.

Candidate relationship parameters:

- `crop_relationship.rotation_compatibility`
- `crop_relationship.previous_crop_effect`
- `crop_relationship.following_crop_effect`
- `crop_relationship.minimum_rotation_interval`
- `crop_relationship.continuous_cropping_risk`
- `crop_relationship.cover_crop_compatibility`
- `crop_relationship.double_crop_compatibility`
- `crop_relationship.intercrop_compatibility`
- `crop_relationship.companion_crop_recommendation`
- `crop_relationship.relay_crop_window`
- `crop_relationship.strip_crop_arrangement`
- `crop_relationship.plant_density_adjustment`
- `crop_relationship.land_equivalent_ratio`
- `crop_relationship.pair_yield_effect`
- `crop_relationship.nutrient_interaction`
- `crop_relationship.pest_disease_interaction`
- `crop_relationship.allelopathy_risk`
- `crop_relationship.harvest_logistics_fit`

Schema-first note:

- The manifest schema is closed. If these are added to `config/parameters/core-crop-parameters.json`, first extend the schema domain enum with a relationship domain such as `cropping_system_relationships`.
- Alternatively, create a separate `config/relationships/relationship-vocabulary.json` with its own schema. This is cleaner if relationship extraction diverges from single-crop parameter extraction.

Recommendation: use a separate relationship vocabulary and relationship-claim schema first. Later, expose selected relationship concepts through the parameter manifest only where they behave like normal searchable parameters.

## Data Model

Prefer a dedicated relationship claim shape instead of forcing pairwise claims into the current normalized claim schema.

The current normalized claim entity model is single-entity oriented. Relationship claims need two crop endpoints, directionality, relationship mode, and pair context. A separate schema avoids breaking existing normalized claims, promotion, coverage, and SQL export.

Proposed required fields:

```json
{
  "relationship_claim_id": "string",
  "run_id": "string",
  "subject_crop": "string",
  "object_crop": "string",
  "subject_crop_group": "string",
  "object_crop_group": "string",
  "relationship_mode": "rotation",
  "relationship_subtype": "previous_crop_effect",
  "direction": "object_precedes_subject",
  "effect": "beneficial",
  "claim_text": "string",
  "value": {},
  "context": {},
  "provenance": {},
  "confidence": "medium",
  "status": "needs_review"
}
```

Context should support:

- `location_scope`
- `source_geo_scope`
- `time_scope`
- `season`
- `growth_stage`
- `management_system`
- `cultivar`
- `arrangement`
- `temporal_offset`
- `disease_pressure`
- `water_regime`
- `input_system`

The relationship claim schema should be closed with explicit optional fields, following the same compatibility lesson as the extraction contract rework.

## Search Pipeline

Add a pair-aware query planner rather than overloading the existing single-crop planner.

### Pair Generation

Generate pair candidates from:

- All ordered crop-profile pairs.
- Self-pairs for continuous cropping.
- Crop-group pairs such as cereal-legume, cereal-cover crop, vegetable-herb, oilseed-cereal, and grass-legume.
- Existing extracted relationship claims, which can seed follow-up searches.
- Curated high-priority pair lists for common systems.

All pairs exist in the coverage matrix. Search scheduling can prioritize likely pairs first.

### Query Templates

Rotation queries:

```text
{subject_crop} after {object_crop} rotation yield disease
{subject_crop} following {object_crop} previous crop effect
{subject_crop} {object_crop} crop sequence rotation interval
{subject_crop} continuous cropping disease break
{subject_crop} rotation with {object_crop} nitrogen credit
```

Intercropping queries:

```text
{subject_crop} {object_crop} intercropping compatibility
{subject_crop} {object_crop} mixed cropping yield
{subject_crop} {object_crop} land equivalent ratio
{subject_crop} {object_crop} row ratio intercrop
{subject_crop} {object_crop} relay cropping window
{subject_crop} {object_crop} companion planting disease
```

Cover/double-crop queries:

```text
{subject_crop} after {object_crop} double crop planting window
{subject_crop} cover crop before {object_crop}
{subject_crop} {object_crop} cover crop compatibility
```

### Run Configuration

Add relationship-search settings only after extending the closed run schema.

Candidate config shape:

```json
{
  "relationship_search": {
    "enabled": true,
    "modes": ["rotation", "intercrop", "cover_crop"],
    "pair_scope": "all_configured_crops",
    "max_pairs": 100,
    "queries_per_pair": 3,
    "include_self_pairs": true,
    "coverage_matrix_path": "exploration/relationships/coverage"
  }
}
```

Budget controls are mandatory. An all-crop matrix grows as `N * N`, and each mode multiplies query count.

### Raw Capture Metadata

Every pair-aware query should preserve query context through raw storage:

```json
{
  "query_kind": "crop_relationship",
  "subject_crop": "corn",
  "object_crop": "soybean",
  "relationship_mode": "intercrop",
  "relationship_subtype": "compatibility",
  "pair_key": "corn|soybean"
}
```

This is important because extraction should know which pair the query targeted, while still being allowed to extract other pairs explicitly mentioned in the source.

## Extraction Pipeline

Add a relationship extractor contract with closed schema and explicit optional fields.

Required extraction fields:

- `subject_crop`
- `object_crop`
- `relationship_mode`
- `relationship_subtype`
- `direction`
- `effect`
- `claim_summary`
- `evidence_text`
- `extraction_confidence`

Optional extraction fields:

- `mechanisms`
- `value_type`
- `numeric_value`
- `range_min`
- `range_max`
- `unit`
- `arrangement`
- `row_ratio`
- `plant_density_adjustment`
- `temporal_offset`
- `management_system`
- `season`
- `growth_stage`
- `cultivar`
- `method`
- `document_id`
- `block_anchor`
- `block_type`
- `page`
- `table_label`

Extraction rules:

- Do not infer relationship claims from generic crop lists.
- Do not turn "grown in the same region" into compatibility.
- Do not label a pair beneficial unless the source states benefit or reports a favorable metric.
- Use `conditional` when benefit depends on row ratio, season, region, irrigation, disease pressure, or cultivar.
- Use `unknown` only in matrix cells, not as an extracted claim unless the source explicitly reports lack of evidence.
- Prefer extracting mechanisms over broad prose when stated.

## Aggregation and Farmer-Facing Matrix

Add a matrix aggregation step:

Input:

- All configured crop pairs.
- Relationship claims.
- Search ledger showing which pair/mode combinations were searched.

Output per cell:

```json
{
  "subject_crop": "corn",
  "object_crop": "soybean",
  "relationship_mode": "rotation",
  "status": "evidence_found",
  "summary_effect": "beneficial",
  "confidence": "medium",
  "evidence_count": 4,
  "mechanisms": ["nitrogen", "disease_break"],
  "best_contexts": ["temperate rainfed", "Midwest"],
  "conflict_count": 1
}
```

The farmer-facing view for one crop is a projection of this matrix:

- Known good partners.
- Conditional partners.
- Avoid or risk partners.
- Rotation-specific predecessors and followers.
- Intercropping-specific simultaneous partners.
- Unknown or unsearched pairs.

## Storage Outputs

Use separate artifacts first:

- `exploration/relationships/query_plans/<run_id>.jsonl`
- `exploration/relationships/raw_claims/<run_id>.jsonl`
- `exploration/relationships/normalized_claims/<run_id>.jsonl`
- `exploration/relationships/review/<run_id>.jsonl`
- `exploration/relationships/matrix/<run_id>.json`
- `exploration/relationships/coverage/<run_id>.json`

PostgreSQL path:

- Add `crop_relationship_claims`.
- Add `crop_relationship_matrix_cells`.
- Keep existing `claims` and promoted single-crop records unchanged.
- Optionally add a view that joins single-crop parameters and relationship matrix summaries for farmer-facing crop pages.

## Phased Implementation

### Phase 0: Plan and Vocabulary

1. Finalize relationship modes, effect labels, directions, and mechanism tags.
2. Decide whether the relationship vocabulary lives in the parameter manifest or a dedicated relationship config.
3. Keep existing rotation parameters unchanged.

Acceptance:

- This plan is approved or amended.
- No pipeline behavior changes.

### Phase 1: Crop Universe and Dense Coverage Matrix

1. Add a crop-universe loader from `config/crops/*.json`.
2. Generate all ordered crop pairs and self-pairs.
3. Write a matrix coverage skeleton with every pair initialized to `not_searched`.
4. Include crop-group placeholder cells for generic relationships.

Acceptance:

- Every configured crop has a row and column.
- Pair count equals `N * N` when self-pairs are included.
- Adding a crop profile expands the matrix without editing code.
- The skeleton matrix does not require live search.

### Phase 2: Schema-First Relationship Artifacts

1. Add `schemas/crop-relationship-claim.schema.json`.
2. Add `schemas/crop-relationship-matrix.schema.json`.
3. Add optional relationship query-context fields to raw capture artifacts, or add a dedicated relationship query-plan artifact.
4. If run configs receive `relationship_search`, extend `schemas/exploration-run.schema.json` before adding config files.

Acceptance:

- Schemas are closed and validate empty/no-evidence matrix outputs.
- Existing single-crop runs and cached extraction outputs remain valid.
- No required fields are added to existing normalized claim outputs.

### Phase 3: Relationship Query Planning

1. Add pair-aware query planning behind a run-config flag.
2. Support `all_configured_crops`, `candidate_pairs`, and explicit pair-list modes.
3. Preserve source-tier coverage across relationship queries.
4. Add query-budget controls with logged truncation.
5. Store pair context in every relationship query plan item and raw capture.

Acceptance:

- A dry-run query plan shows pair, mode, source tier, and rendered query.
- Budgeting is deterministic and logged.
- Existing `plan-queries` output for normal crop runs is unchanged unless relationship search is enabled.

### Phase 4: Relationship Extraction and Normalization

1. Add a relationship extractor schema and validator.
2. Normalize relationship outputs into relationship claims.
3. Carry pair context, source tier, block/table provenance, and scope.
4. Add review rules for conflict grouping by subject, object, mode, subtype, scope, and arrangement.

Acceptance:

- Fixture extraction covers one rotation pair, one self-rotation interval, one intercropping pair, and one no-claim source.
- Invalid crop names, unsupported effect labels, and missing evidence text are rejected.
- Relationship claims do not pollute the existing single-crop normalized claim schema.

### Phase 5: Rotation Matrix Bridge

1. Map existing rotation-related parameters to relationship-matrix summaries.
2. Keep `management.rotation_recommendation` as the high-level fallback for broad rotation sources.
3. Promote pair-specific rotation evidence into matrix cells.
4. Generate per-crop rotation projections from the matrix.

Acceptance:

- Existing rotation searches still work.
- Pair-specific rotation claims are visible in matrix output.
- A crop page can show "good predecessors", "avoid predecessors", and "minimum break interval" from matrix cells.

### Phase 6: Intercropping and Concurrent-Cropping Pilot

1. Enable `intercrop`, `relay_crop`, `strip_crop`, `mixed_crop`, and `companion_crop` modes for a small pair set.
2. Extract compatibility, arrangement, density adjustment, LER, yield effect, and mechanisms.
3. Add review gates that distinguish scientific intercropping evidence from anecdotal companion planting claims.

Acceptance:

- At least one pair produces a structured concurrent-cropping claim.
- Companion planting claims are lower confidence unless supported by extension, institutional, or peer-reviewed evidence.
- LER and yield-effect values preserve units and context.

### Phase 7: Full-Matrix Scaling

1. Run all configured crop pairs in batches.
2. Report matrix coverage by crop, crop group, relationship mode, source tier, and evidence status.
3. Prioritize unsearched or conflicting cells for future runs.
4. Add crop-group fallback summaries where pair-level evidence is sparse.

Acceptance:

- Every configured pair has one of the defined matrix statuses.
- No unsearched cell is presented as neutral.
- The farmer-facing view distinguishes known good, conditional, avoid, searched-no-evidence, and not-searched pairs.

## Compatibility Answer

This should not break the upstream extraction pipeline if implemented as a separate staged relationship path.

Safe:

- Add this planning document.
- Add relationship schemas and empty matrix artifacts.
- Add a relationship query planner behind a disabled-by-default run flag.
- Add relationship extraction outputs in separate artifact paths.
- Keep existing `management.rotation_*` parameter IDs and semantics.

Risky or breaking:

- Changing the existing normalized claim `entity` shape to require crop pairs.
- Adding relationship fields to closed schemas without schema-first changes.
- Promoting many relationship parameters to `active` before query and extraction fixtures exist.
- Treating all unobserved pairs as neutral or incompatible.
- Running all pair/mode/source-tier queries without explicit budgets.

The safe posture is: dense coverage ledger, sparse evidence graph, budgeted activation.

## Validation Plan

For docs-only planning:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map
PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff
```

For future implementation:

- Validate all new schemas.
- Add unit tests for crop-pair generation and all-crop matrix coverage.
- Add fixture tests for relationship extraction validation.
- Add dry-run query plan snapshots.
- Add one no-evidence fixture to prove the matrix records `searched_no_evidence` without fabricating a relationship.

## Open Design Questions

1. Should relationship concepts live in the core parameter manifest or a separate relationship vocabulary?
2. Should crop-group cells, such as `corn -> legumes`, be first-class cells or only aggregated rollups?
3. Should derived compatibility scores be exported, and if so, what formula weights source tier, evidence count, recency, and conflict?
4. Should anecdotal companion planting be allowed at low confidence, or excluded until supported by extension/institutional/scientific sources?
5. Should relationship claims be rendered into the Obsidian vault as pair notes, crop pages, or both?

## Recommended First Slice

Start with a non-breaking matrix skeleton:

1. Add relationship vocabulary and matrix schemas.
2. Generate all ordered pairs for the configured crop profiles.
3. Write a matrix skeleton where every cell is `not_searched`.
4. Add a dry-run relationship query planner for rotation mode only.
5. Bridge rotation summaries from the matrix without changing existing single-crop extraction.

That gives complete all-crop coverage at the structural level before any live search or extraction risk is introduced.
