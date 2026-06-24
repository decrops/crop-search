# Crop Parameter Ontology Expansion Plan

Status: Foundation implemented; active expansion still gated
Date: 2026-06-24
Related plans:
- `.planning/generalized-crop-ontology-plan.md`
- `.planning/pipeline-quality-rework-plan.md`
- `.planning/opus-vault-extraction-plan.md`
- `.planning/extraction-contract.md`

## Goal

Widen the crop parameter ontology so the system covers more farmer-relevant decision areas without degrading search quality, extraction precision, normalization, promotion review, or coverage metrics.

The expansion should keep a strict distinction between:

- **Candidate concept**: agronomically useful idea that may belong in the ontology.
- **Stub/deferred parameter**: visible backlog item, not searched and not counted in yield metrics.
- **Active parameter**: searched, included in the extractor parameter enum, counted in coverage, and eligible for normalized/promotion output.

## Current Baseline

The current core manifest is `config/parameters/core-crop-parameters.json` at `manifest_version: 0.4.0`.

Current shape:

- 126 total parameters.
- 85 `active` parameters.
- 34 `stub` parameters.
- 7 `deferred` parameters.
- Active domains are strongest in physiology, growth monitoring, abiotic stress, water, soil, nutrients, planting, quality, harvest, and canopy/root traits.
- Weak or placeholder-only domains:
  - `variety_cultivar`: 0 active, 8 stubs.
  - `crop_protection`: 0 active, 12 stubs.
  - `economics`: 0 active, 1 stub and 7 deferred.
  - `post_harvest_quality`: 4 active and 7 stubs; storage/drying remains a strong activation candidate.

The search planner currently selects parameters from the core manifest only. It skips all entries whose `implementation_status` is not `active`, then filters by run-config `parameter_ids`, `parameter_families`, crop group applicability, and `max_parameters`.

The LLM extraction path also derives its allowed `parameter_id` enum from active manifest entries only. Stubs and deferred items are not valid extraction targets.

Two validation constraints matter for this plan:

- `schemas/parameter-manifest.schema.json` is closed (`additionalProperties: false`) at the per-parameter level. New authoring metadata must always be schema-first.
- `src/crop_search_framework/llm_extract.py` now separates required extraction keys from optional extension keys. New optional fields are schema-declared and backfilled for old cached outputs.

## Implemented Foundation

Implemented on 2026-06-24:

- Extended `schemas/parameter-manifest.schema.json` with optional authoring metadata fields: `query_units`, `query_terms`, `stage_terms`, `expected_value_shape`, `expected_scope`, `false_positive_terms`, and `extraction_notes`.
- Bumped the core manifest to `manifest_version: 0.4.0`.
- Added 34 non-active candidate parameters across cultivar/variety, crop protection, post-harvest/storage, economics, and management deepening.
- Kept the active set at 85, preserving global query counts and extraction enum size.
- Added parameter-level query metadata to selected active parameters.
- Added optional extraction-field machinery: `REQUIRED_EXTRACTION_KEYS`, `OPTIONAL_EXTRACTION_KEYS`, closed output schema with optional properties, old-cache backfilling, and normalized provenance carry-through.
- Fixed the Python 3.8 `Path.is_relative_to` eval-harness compatibility issue.

Still gated:

- Promote the first 10-15 new parameters to `active`.
- Add retrieval/extraction gold cases for the first active batch.
- Run small live query spikes before accepting the expanded active set.

## Compatibility Answer

This rework should **not** break the upstream extraction pipeline if it remains staged and schema-first. "Additive" is safe only when the relevant closed schemas and validators are extended before new fields appear in artifacts.

Safe changes:

- Add new concepts as `implementation_status: stub` or `deferred`.
- Extend the closed manifest schema with optional authoring metadata, then populate records with those schema-declared fields, such as `query_units`, `query_terms`, `expected_value_shape`, or `extraction_notes`.
- Add crop profile metadata such as aliases, scientific names, stage terms, and BBCH mappings.
- Add planning/backlog markdown files.
- Add new active parameters only in small batches after query preview, extraction fixture checks, and coverage denominator review.

Risky or breaking changes:

- Renaming or deleting existing `parameter_id` values. Existing cached extractions, normalized claims, vault notes, and coverage artifacts use those IDs.
- Changing existing `normalized_attribute` or `normalized_attribute_subtype` semantics without a migration.
- Adding new manifest fields to parameter records before extending `parameter-manifest.schema.json`; validation fails immediately because the schema is closed.
- Adding required manifest fields without defaulting old records.
- Moving many new parameters directly to `active`; this increases query counts, the extraction enum, coverage denominators, prompt size, and review burden all at once.
- Making broad/fuzzy concepts active, such as `weed_management` or `input_intensity`, before they are split into extractable atomic parameters.
- Adding crop-specific `crop_parameters` overlays and expecting them to be searched before `selected_parameters()` is extended to merge the overlay.
- Extending required extraction keys instead of optional extraction keys. Optional-field machinery now exists; new extensions should use it unless a deliberate cache migration is planned.

Expected effects when a new parameter becomes active:

- `plan-queries` emits new searches for every applicable source tier.
- The active parameter enum in the extractor grows.
- Coverage denominators grow.
- Existing cached LLM outputs will not magically contain the new ID; the new parameter needs fresh extraction or replay from documents.
- Tests that intentionally assert the active count, such as source-tier/query-count tests, must be updated with the new expected count.
- Promotion may still reject or park claims if review rules cannot distinguish canonical values from broad descriptive text.

Therefore the safe posture is: **author broadly, activate narrowly**.

## Authoring Criteria

A parameter should graduate to `active` only if it satisfies all of these:

1. **Decision relevance**: a farmer, advisor, breeder, crop model, or downstream app can use it.
2. **Searchability**: source documents use predictable phrases for it.
3. **Extractability**: sources state it as a value, range, threshold, timing, category, class, or recommendation.
4. **Scope clarity**: applicability is clear enough to represent crop, crop group, region, growth stage, cultivar, season, or management system.
5. **Normalizability**: units, categorical labels, or text values can be compared across sources.
6. **Reviewability**: conflicts can be grouped and adjudicated without collapsing unlike claims.

If any criterion is weak, the concept remains `stub` or `deferred`.

## Parameter Design Rules

Prefer atomic parameters over broad concepts.

Good active candidates:

- `crop_protection.critical_weed_free_period`
- `crop_protection.insect_action_threshold`
- `crop_protection.fungicide_application_timing`
- `variety_cultivar.maturity_class`
- `variety_cultivar.winter_hardiness`
- `variety_cultivar.lodging_resistance`
- `post_harvest_quality.safe_storage_moisture`
- `post_harvest_quality.safe_storage_temperature`

Poor active candidates until split:

- `crop_protection.weed_management`
- `crop_protection.disease_management`
- `economics.input_intensity`
- `variety_cultivar.adaptation`

Use broad concepts as stubs, then split them into search/extraction-ready children.

## Proposed Expansion Areas

### Variety and Cultivar

Priority: medium-high.

Reason: farmer decision value is high, but values are often cultivar- and region-specific.

Candidate parameters:

- `variety_cultivar.maturity_class`
- `variety_cultivar.winter_hardiness`
- `variety_cultivar.lodging_resistance`
- `variety_cultivar.disease_resistance`
- `variety_cultivar.quality_class`
- `variety_cultivar.photoperiod_sensitivity`
- `variety_cultivar.vernalization_class`

Activation guidance:

- Activate only parameters that can carry `cultivar` and region scope.
- Avoid promoting broad variety-list claims as canonical crop facts.
- Prefer extension variety guides, national recommended-list trials, and cultivar release papers.

### Crop Protection

Priority: high.

Reason: current ontology has no active crop-protection parameters, but pest, disease, and weed timing are central farmer decisions.

Candidate parameters:

- `crop_protection.key_disease_pressure`
- `crop_protection.disease_sensitive_stage`
- `crop_protection.fungicide_application_timing`
- `crop_protection.insect_action_threshold`
- `crop_protection.pest_sensitive_stage`
- `crop_protection.critical_weed_free_period`
- `crop_protection.herbicide_application_window`
- `crop_protection.seed_treatment_recommendation`

Activation guidance:

- Split organism tagging from the parameter itself. The parameter is the claim type; organism names belong in optional extraction fields or provenance tags.
- Require `organism`/`pest` tagging before broad IPM parameters are promoted.
- Treat thresholds as regional and management-specific unless sources clearly state general applicability.

### Post-Harvest and Storage

Priority: high.

Reason: often expressed with clean values, units, and actionable thresholds.

Candidate parameters:

- `post_harvest_quality.safe_storage_moisture`
- `post_harvest_quality.safe_storage_temperature`
- `post_harvest_quality.drying_temperature_limit`
- `post_harvest_quality.storage_duration`
- `post_harvest_quality.test_weight_standard`
- `post_harvest_quality.quality_grade_threshold`

Activation guidance:

- These are good early active candidates because evidence is often table-like and unit-bearing.
- Ensure units distinguish grain moisture percent, air temperature, and storage duration.

### Economics

Priority: medium, but mostly deferred.

Reason: farmer value is high, but extraction is risky because values are currency-, year-, region-, and system-specific.

Candidate parameters:

- `economics.seed_cost`
- `economics.fertilizer_cost`
- `economics.irrigation_cost`
- `economics.pesticide_cost`
- `economics.harvest_cost`
- `economics.expected_gross_margin`
- `economics.break_even_yield`

Activation guidance:

- Keep most economics parameters `deferred` until the normalized value model can carry `currency`, `price_year`, `area_unit`, and production system.
- Prefer extracting economics into regional/year-scoped records, never canonical crop facts.

### Management Deepening

Priority: medium.

Candidate parameters:

- `management.rotation_interval`
- `management.cover_crop_compatibility`
- `management.residue_management`
- `management.grazing_window`
- `management.double_crop_window`
- `management.irrigation_method`

Activation guidance:

- Avoid fuzzy text-only recommendations unless they support a clear farmer decision.
- Stage, region, and management-system scope are usually required.

## Manifest Metadata Improvements

Add optional manifest fields before activating many new parameters:

```json
{
  "query_units": ["kg/ha", "lb/ac"],
  "query_terms": ["N rate", "split application", "topdress"],
  "expected_value_shape": "numeric_range",
  "extraction_notes": "Extract only explicit crop-specific values; ignore general background statements.",
  "false_positive_terms": ["animal feed", "human nutrition"]
}
```

Suggested optional fields:

- `query_units`: unit tokens to use in search.
- `query_terms`: domain idioms and high-signal source phrases.
- `stage_terms`: parameter-specific growth-stage phrases.
- `expected_value_shape`: `numeric`, `range`, `date_window`, `stage`, `category`, `boolean`, or `text`.
- `expected_scope`: expected applicability fields, such as `region`, `bbch`, `cultivar`, or `management_system`.
- `false_positive_terms`: common sources of retrieval/extraction noise.
- `extraction_notes`: short instructions for the extractor and review/gold-set authors.

Because the manifest schema is closed, these fields require a schema-first change. The safe sequence is:

1. Extend `parameter-manifest.schema.json` `properties` with optional fields.
2. Add tests proving old records without those fields still validate.
3. Populate manifest records with only schema-declared metadata fields.
4. Validate the manifest in the same change.

## Phased Implementation

### Phase 0: Baseline and Inventory

1. Generate the current domain/status matrix.
2. Record the current active count and query count for wheat, rice, sunflower, and tomato.
3. Identify active-count assertions in tests so expected-count updates are deliberate.
4. Create a candidate backlog grouped by domain.

Acceptance:

- Current baseline captured before any manifest edits.
- No active count changes.

### Phase 1: Stub/Deferred Expansion

1. Add 30-50 candidate parameters as `stub` or `deferred`.
2. Prioritize crop protection, cultivar, storage, and economics.
3. Ensure every new record has `domain`, `parameter_kind`, `concept_scope`, `decision`, and `requires_stage_context`.
4. Do not include stubs in query planning or extraction enums.

Acceptance:

- Manifest schema validates.
- `plan-queries` output is unchanged for existing global run configs.
- Extractor active enum count is unchanged.
- Coverage denominator is unchanged.

### Phase 2: Query and Extraction Metadata

1. Extend `schemas/parameter-manifest.schema.json` first. This is mandatory because per-parameter records have `additionalProperties: false`.
2. Add optional schema fields for `query_units`, `query_terms`, `expected_value_shape`, `expected_scope`, `false_positive_terms`, and `extraction_notes`.
3. In the same commit, or a later commit, populate manifest records only with fields already declared in the schema.
4. Populate these fields for existing high-value active parameters and the most promising stubs.
5. Update query generation to use units and terms once `pipeline-quality-rework-plan.md` WS-5 is implemented.

Acceptance:

- Old manifest records without the new optional fields remain valid.
- Metadata-bearing manifest records validate because the schema change landed before or with the metadata.
- No unschematized metadata fields appear in `config/parameters/core-crop-parameters.json`.
- Query snapshots show improved terms without exploding query volume.
- Retrieval gold-set scores improve or stay neutral.

### Phase 3: Small Active Batch

Promote only 10-15 parameters to `active` in the first widening batch.

Recommended first batch:

- `crop_protection.critical_weed_free_period`
- `crop_protection.insect_action_threshold`
- `crop_protection.fungicide_application_timing`
- `crop_protection.disease_sensitive_stage`
- `post_harvest_quality.safe_storage_moisture`
- `post_harvest_quality.safe_storage_temperature`
- `post_harvest_quality.drying_temperature_limit`
- `variety_cultivar.maturity_class`
- `variety_cultivar.lodging_resistance`
- `variety_cultivar.disease_resistance`

Acceptance:

- Manifest version bumped.
- Tests updated for the new active count and query count. Current known active-count pin: `tests/test_source_tiers.py` asserts 85 wheat-active parameters and therefore 425 tiered queries. `tests/test_llm_extract.py` is already tolerant with `>= 80`.
- `plan-queries` reviewed manually for wheat.
- Extraction gold examples added for at least crop protection and storage.
- A small wheat/rice/tomato query spike finds at least one relevant source for most new active parameters.

### Phase 4: Extraction Contract Extensions

Add optional extraction fields only if needed by the new domains.

Implemented prerequisite:

- `REQUIRED_EXTRACTION_KEYS` and `OPTIONAL_EXTRACTION_KEYS` now exist.
- The model output schema remains closed, but only the core keys are required.
- `validate_extraction_claims()` / `_coerce_claim()` accept old cached outputs missing optional fields and backfill optional keys to `None`.
- Tests cover strict schema properties, old-cache replay, optional-field preservation, and normalized provenance carry-through.

Remaining rule:

- Add future extraction fields only through `OPTIONAL_EXTRACTION_KEYS` unless there is an explicit migration plan for old cached outputs.

Recommended additive fields:

- `organisms`: pests, diseases, weeds, pathogens, or beneficial organisms mentioned by the source.
- `method`: trial, guideline, model, lab assay, survey, budget, cultivar trial.
- `price_year`, `currency`, `area_unit`: only before activating economics.
- `block_anchor`, `block_type`, `page`, `table_label`: aligned with the block-store extraction plan.

Acceptance:

- Old cached extraction outputs remain valid because the optional-field validator rework landed before any new field.
- New fields are optional and carried through normalized provenance or dedicated scope fields.
- Review can use organism/method/block provenance without requiring it for old claims.

### Phase 5: Evaluation-Gated Scaling

1. Run retrieval eval before and after query-template changes.
2. Run extraction eval for documents known to contain new-domain claims.
3. Compare coverage status without treating new active parameters as a failure unless they had at least one plausible source.
4. Promote a second active batch only after the first batch produces useful normalized claims.

Acceptance:

- Per-domain retrieval recall does not regress.
- Extraction precision stays high for existing physiology/nutrient/water domains.
- New domains produce normalized claims that survive review, not only noisy candidates.

## Validation Commands

After a manifest/schema change:

```bash
PYTHONPATH=src python3 -m crop_search_framework.cli validate parameter-manifest.schema.json config/parameters/core-crop-parameters.json
PYTHONPATH=src python3 -m crop_search_framework.cli plan-queries --run-config config/runs/pilot-global-wheat.json
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall src tests
```

After a capability or generated-doc change:

```bash
PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map
PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff
```

After an active-parameter batch:

```bash
PYTHONPATH=src python3 -m crop_search_framework.cli eval-retrieval <run_id>
PYTHONPATH=src python3 -m crop_search_framework.cli eval-extraction <run_id>
```

Use the actual run ID once the discovery ledger/fetch queue for that run exists.

## Rollback Strategy

If a new active batch hurts retrieval or extraction quality:

1. Do not delete the parameter IDs.
2. Move weak entries back to `stub` or `deferred`.
3. Keep the manifest version history in the implementation log.
4. Mark cached artifacts with the manifest version they targeted.
5. Re-run query planning and extraction only for the affected batch.

This preserves old claim provenance and avoids orphaning normalized records.

## Decision Summary

The ontology should widen, but activation must stay narrow and evaluated. Broad stubs are useful because they make the decision map visible. Active parameters are a contract with search, extraction, normalization, review, vault rendering, coverage, and downstream storage.

This rework does not need to break the upstream extraction pipeline. It becomes risky only when new concepts are promoted to `active` without query terms, expected value shapes, extraction examples, and review rules.
