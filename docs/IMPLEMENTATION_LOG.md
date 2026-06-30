# Implementation Log

This log records implementation milestones for the crop-search runner and data pipeline.

## 2026-05-29: Generic Parameter Manifest

- Added `schemas/parameter-manifest.schema.json` for reusable crop physiology and management parameter definitions.
- Added `schemas/crop-profile.schema.json` for crop-specific aliases, growth-stage terms, and source-bias terms.
- Added `schemas/parameter-coverage.schema.json` for parameter coverage reporting.
- Added `config/parameters/core-crop-parameters.json` with generic parameter families: temperature, thermal time, water, soil, phenology, planting, nutrients, stress, harvest, and management.
- Added crop profiles for corn, soybean, wheat, rice, and cotton under `config/crops/`.
- Updated `config/runs/pilot-us-corn-iowa.json` so the pilot can generate searches from the parameter manifest and corn crop profile.
- Added `crop-framework plan-queries` to render parameter-driven searches without fetching sources.
- Updated `run-exploration` to attach parameter metadata to generated queries and raw captures.
- Added `parameter_id` to normalized, review, durable, and PostgreSQL records.
- Added `crop-framework coverage-run` to report which requested parameters were promoted, candidate-only, needs-review, or missing.
- Added `exploration/coverage/` as the coverage artifact area.
- Updated PostgreSQL schema and migrations to index `parameter_id` for normalized and durable claims.
- Extended CI to validate parameter/crop schemas, render query plans, run coverage, and validate durable/coverage outputs.

Current pilot coverage from existing captures:

- Requested parameters: 12
- Parameters with normalized claims: 9
- Parameters with promoted claims: 7
- Parameters missing: 3
- Parameters needing review: 1

Note: the existing raw capture summary for `pilot-us-corn-iowa-001` came from the earlier three-query live pilot. The runner now renders a 12-query manifest-driven plan; the next live exploration run will refresh raw artifacts with parameter metadata in the raw summary and captures.

## 2026-05-29: Multi-Crop Manifest Rerun

- Added crop profiles for sunflower and tomato.
- Added country-level United States run configs for wheat, rice, sunflower, and tomato.
- Added optional `source_seeds` to exploration run configs so trusted live source URLs can be fetched when live search returns no accepted results.
- Updated exploration runs to clear prior generated raw captures before rerun so stale captures do not contaminate normalization.
- Changed source de-duplication from source-only to source-plus-parameter so the same high-value guide can be parsed under different parameter query contexts.
- Added search failure handling so a hard search-tool failure is recorded and source seeds can still carry the run forward.
- Added source-seed ranking so exact parameter seeds are preferred over broad crop guide seeds.

Rerun results:

| Run | Raw captures | Candidate claims | Search failures | Source failures | Normalized claims | Promoted claims | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `pilot-us-wheat-001` | 22 | 176 | 0 | 0 | 174 | 15 | 3 normalized / 2 promoted / 9 missing |
| `pilot-us-rice-001` | 24 | 192 | 12 | 0 | 192 | 15 | 3 normalized / 3 promoted / 9 missing |
| `pilot-us-sunflower-001` | 23 | 184 | 12 | 0 | 184 | 33 | 5 normalized / 1 promoted / 7 missing |
| `pilot-us-tomato-001` | 23 | 184 | 12 | 0 | 184 | 27 | 4 normalized / 3 promoted / 8 missing |

Generated artifact roots:

- `exploration/raw/pilot-us-wheat-001/`
- `exploration/raw/pilot-us-rice-001/`
- `exploration/raw/pilot-us-sunflower-001/`
- `exploration/raw/pilot-us-tomato-001/`
- `exploration/normalized/<run_id>/`
- `exploration/review/<run_id>/`
- `exploration/coverage/<run_id>/`
- `memory/durable/<run_id>/`

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall src tests`
- Validated generated raw captures, normalized claims, review records, durable claims, and coverage records for all four rerun crops.

Current constraints surfaced by the rerun:

- DuckDuckGo HTML search became unreliable during the run, returning empty responses and then 403 errors. The seed fallback preserved live fetch/parse execution, but production search needs a durable provider or provider rotation.
- Coverage is still sparse for water and soil parameters, especially when a broad guide contains relevant text but the heuristic parser selects repeated generic temperature claims.
- Several useful claims are still landing in `unmapped.*` buckets, especially soil temperature thresholds and general moisture requirements.
- Manual review remains high for wheat and tomato because repeated captures from the same broad guides create many low-specificity claims.

## 2026-05-29: Geolocation Extraction Split

- Extended normalized claims with `source_geo_scope` and `geo_evidence`.
- Kept `location_scope` as claim applicability and separated it from source/document origin geography.
- Expanded state detection beyond the first corn/Iowa pilot aliases to a full U.S. state alias set.
- Added source-origin detection from source title, URL, domain, and parsed document snippet.
- Propagated geolocation fields into durable claims and PostgreSQL export/load SQL.
- Added PostgreSQL migration `003_geolocation_scopes.sql`.
- Regenerated normalized, review, durable, and coverage artifacts for corn, wheat, rice, sunflower, and tomato.
- Added local geocoding with stable IDs, EPSG:4326 centroid latitude/longitude, optional bounding boxes, geocode source, and confidence.
- Added PostgreSQL coordinate columns for normalized claim and source scopes: `location_centroid_lat`, `location_centroid_lon`, `source_centroid_lat`, and `source_centroid_lon`.

Post-regeneration geolocation summary:

| Run | Claim-location scopes | Source-origin scopes |
| --- | --- | --- |
| `pilot-us-corn-iowa-001` | 21 global, 8 Iowa, 1 farm, 1 southern Corn Belt, 1 United States, 1 Nebraska | Iowa, Corn Belt, Kansas, global |
| `pilot-us-wheat-001` | 93 global, 72 Kansas, 7 South Dakota, 1 United States, 1 Oregon | Kansas, South Dakota, Nebraska, global |
| `pilot-us-rice-001` | 192 global | Arkansas |
| `pilot-us-sunflower-001` | 160 global, 24 North Dakota | North Dakota, Texas |
| `pilot-us-tomato-001` | 184 global | Georgia, United States, global |

Coordinate coverage after geocoding:

| Run | Claim scopes with coordinates | Source scopes with coordinates |
| --- | ---: | ---: |
| `pilot-us-corn-iowa-001` | 12 / 33 | 29 / 33 |
| `pilot-us-wheat-001` | 81 / 174 | 159 / 174 |
| `pilot-us-rice-001` | 0 / 192 | 192 / 192 |
| `pilot-us-sunflower-001` | 24 / 184 | 184 / 184 |
| `pilot-us-tomato-001` | 0 / 184 | 168 / 184 |

Example coordinate readouts:

- `pilot-us-corn-iowa-001-capture-001-claim-001` has farm-level claim coordinates for `ISU Northeast Research Farm, Nashua, Iowa`: `lat=42.93628`, `lon=-92.5688524`, `geo_id=osm:way:15896307`.
- `pilot-us-tomato-001-capture-001-claim-002` remains `location_scope=global`, but has `source_geo_scope=Georgia` with `lat=32.1656`, `lon=-82.9001`, `geo_id=iso3166-2:US-GA`.

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall src tests`
- Validated normalized claims, review reports, durable claims, and coverage reports for the regenerated runs.
- Exported PostgreSQL SQL for corn, wheat, rice, sunflower, and tomato with the new geolocation columns.

## 2026-05-30: Precision Geocoding and Header Cleanup

- Added 2025 U.S. Census Gazetteer source files under `data/gazetteer/` for state and county geocoding.
- Replaced hard-coded U.S. state centroid records with Census-backed state records using stable `census:*` `geo_id` values and Census internal-point latitude/longitude.
- Added county geocoding support for state-qualified county mentions such as `Chickasaw County, Iowa`.
- Expanded named production-region matching for crop regions such as `Northern Great Plains`, `Texas High Plains`, `Mississippi Delta`, `Arkansas Grand Prairie`, `Pacific Northwest`, `Central Valley`, `Southeast`, and `Mid-Atlantic`.
- Kept named production regions as custom approximate records because production-region boundaries are contextual and not a single authoritative geometry.
- Hardened parser and normalizer cleanup for source headers, author/byline fragments, navigation sentences, short section headings, and table captions.
- Regenerated normalized, review, durable, coverage, and PostgreSQL SQL artifacts from the existing raw captures.

Quality movement from the pre-hardening artifact baseline:

| Run | Normalized before | Normalized after | Manual review before | Manual review after | Promoted after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `pilot-us-corn-iowa-001` | 33 | 33 | 11 | 11 | 12 |
| `pilot-us-wheat-001` | 174 | 124 | 151 | 102 | 15 |
| `pilot-us-rice-001` | 192 | 80 | 56 | 0 | 15 |
| `pilot-us-sunflower-001` | 184 | 160 | 67 | 67 | 33 |
| `pilot-us-tomato-001` | 184 | 182 | 157 | 155 | 27 |

Coordinate coverage after precision hardening:

| Run | Claim scopes with coordinates | Source scopes with coordinates |
| --- | ---: | ---: |
| `pilot-us-corn-iowa-001` | 12 / 33 | 29 / 33 |
| `pilot-us-wheat-001` | 44 / 124 | 111 / 124 |
| `pilot-us-rice-001` | 0 / 80 | 80 / 80 |
| `pilot-us-sunflower-001` | 12 / 160 | 160 / 160 |
| `pilot-us-tomato-001` | 0 / 182 | 168 / 182 |

Example coordinate readouts after the Census switch:

- `pilot-us-wheat-001-capture-002-claim-001` has claim and source scope `South Dakota` with `lat=44.446796`, `lon=-100.238176`, `geo_id=census:0400000US46`.
- `pilot-us-tomato-001-capture-001-claim-002` remains `location_scope=global`, but has `source_geo_scope=Georgia` with `lat=32.629579`, `lon=-83.423511`, `geo_id=census:0400000US13`.
- `pilot-us-corn-iowa-001-capture-001-claim-001` still has exact farm-level claim coordinates for `ISU Northeast Research Farm, Nashua, Iowa`: `lat=42.93628`, `lon=-92.5688524`, `geo_id=osm:way:15896307`.

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall src tests`
- Validated 579 regenerated normalized claims, 5 review reports, 5 durable claim reports, and 5 coverage reports against their schemas.

Current remaining constraint:

- Existing raw captures did not contain state-qualified county claims after cleanup, so county extraction is implemented and test-covered but not yet represented in the current pilot artifacts.

## 2026-05-30: Source Tiers and Global Search Scope

- Added `schemas/source-tier-manifest.schema.json`.
- Added `config/source-tiers/default.json` with five evidence tiers: peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides.
- Updated the source policy to explicitly search all legally accessible evidence tiers while limiting paywalled papers/books to metadata or accessible text.
- Updated query planning so tier-aware runs generate parameter-specific queries per source tier rather than appending only `extension agronomy`.
- Updated `plan-queries`, raw capture metadata, exploration summaries, and source-seed matching to carry `source_tier_id` and `source_tier_label`.
- Added source-tier scoring boosts for scientific, textbook/reference, international-institution, extension/public agronomy, and industry/grower sources.
- Added global run configs for wheat, rice, sunflower, and tomato under `config/runs/pilot-global-*.json`.
- Set the global run configs to `region_scope=global` and `use_source_seeds=false` so the next search does not silently fall back to the previous U.S.-centric seed list.

Global wheat query-plan check:

- `PYTHONPATH=src python3 -m crop_search_framework.cli plan-queries --run-config config/runs/pilot-global-wheat.json`
- Result: 60 planned queries = 12 parameters x 5 source tiers.
- Example tiers represented: `peer_reviewed_science`, `textbook_reference`, `international_institution`, `extension_publication`, and `industry_grower_guide`.

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- Validated `config/source-tiers/default.json` and all run configs against their schemas.

## 2026-05-30: Session Handoff Automation

- Added `AGENTS.md` with repo-level instructions for future sessions to read `docs/HANDOFF.md` first and refresh it before ending meaningful implementation work.
- Added `src/crop_search_framework/handoff.py` to render a current project handoff from run configs, artifact summaries, validation expectations, and known constraints.
- Added `crop-framework write-handoff` / `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff` to regenerate `docs/HANDOFF.md`.
- Linked the handoff workflow from `README.md`.

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff`

## 2026-05-30: Capability Map Automation

- Added `src/crop_search_framework/capabilities.py` to render a generated capability map from current run configs, artifact summaries, and known pipeline limits.
- Added `crop-framework write-capability-map` / `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map` to regenerate `docs/CAPABILITY_MAP.md`.
- Updated `AGENTS.md` so future sessions read `docs/CAPABILITY_MAP.md` after the handoff and refresh it whenever capabilities or constraints change.
- Updated the generated handoff workflow to reference the capability map and refresh it before `write-handoff`.
- Linked the capability-map workflow from `README.md`.

Validation:

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map`
- `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff`

## 2026-06-02: Tier Metrics and Scholarly/Book Discovery

- Added source-tier metrics to exploration summaries: planned queries, search hits, seed hits, results returned, fetch successes, parse successes, metadata-only captures, captured sources, candidate claims, and failures by tier.
- Added `discovery_method`, `access_status`, and `source_metadata` to raw captures, and propagated `source_tier_id`, `source_tier_label`, discovery method, and access status into normalized claim provenance.
- Added connector-aware discovery for `peer_reviewed_science` and `textbook_reference` tiers:
  - OpenAlex and Crossref for scholarly metadata and open-access paper URLs.
  - Google Books and Open Library for book/reference metadata and open archive/readable URLs.
  - DuckDuckGo HTML remains the fallback discovery source.
- Added metadata-only capture handling so paywalled or unavailable papers/books can be retained for discovery provenance without fetching or extracting inaccessible text.
- Extended coverage reports with `source_tier_summary`, per-parameter `source_tier_counts`, and `science_textbook_status`.
- Improved parser cleanup for PDF/book/scientific documents by filtering likely reference entries, index entries, table-like lines, front/back matter headings, and adding lightweight evidence-fragment labels.
- Updated `config/mcp/servers.local.json` to describe the expanded discovery provider set.
- Updated `README.md` to describe connector-aware live discovery and source-tier coverage reporting.
- Regenerated coverage reports for the existing U.S. pilot artifacts under `exploration/coverage/`.

Validation:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `PYTHONPATH=src .venv/bin/python -m compileall src tests`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli plan-queries --run-config config/runs/pilot-global-wheat.json`
- Regenerated coverage for `pilot-us-corn-iowa-001`, `pilot-us-wheat-001`, `pilot-us-rice-001`, `pilot-us-sunflower-001`, and `pilot-us-tomato-001`.

Current remaining constraints:

- Connector results still need a fresh global live run to measure actual peer-reviewed and textbook yield by tier.
- Semantic Scholar/PubMed are not yet implemented.
- Scientific article and book parsing remains heuristic; metadata-only records are intentionally not extracted into claims unless accessible text is available.

## 2026-06-04: Global Wheat End-to-End Tier Benchmark

- Ran `pilot-global-wheat-001` through the full pipeline: exploration, normalization, review, promotion, coverage, and PostgreSQL SQL export.
- Fixed tool subprocess execution so manifest commands using `python` or `python3` resolve to the active interpreter via `sys.executable`; this keeps discovery/fetch/parse tools inside the same virtual environment as the CLI.
- Exported PostgreSQL SQL to `data/postgres/load-pilot-global-wheat-001.sql`; no `POSTGRES_DSN` was configured, so the loader used SQL export mode.

Exploration summary:

- Queries executed: 60
- Unique sources captured: 88
- Candidate claims: 460
- Search failures: 0
- Source failures: 61
- Access statuses: 60 open full text, 28 metadata only
- Discovery methods: 59 DuckDuckGo HTML, 28 Crossref, 1 OpenAlex

Source-tier movement:

| Source tier | Captured sources | Open full text | Metadata only | Candidate claims | Normalized claims | Promoted claims | Needs review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Extension/public agronomy | 9 | 9 | 0 | 72 | 70 | 1 | 47 |
| Industry/grower guides | 11 | 11 | 0 | 83 | 82 | 0 | 39 |
| International institutions | 19 | 19 | 0 | 152 | 112 | 0 | 42 |
| Peer-reviewed science | 29 | 1 | 28 | 2 | 0 | 0 | 0 |
| Textbooks/reference books | 20 | 20 | 0 | 151 | 119 | 0 | 66 |

Pipeline outputs:

- Normalized claims: 383
- Manual-review claims: 194
- Seasonal-observation claims: 131
- Rejected claims: 57
- Promoted durable claims: 1
- Parameters requested: 12
- Parameters with normalized claims: 8
- Parameters with promoted claims: 1
- Parameters missing: 4
- Parameters needing review: 5
- Parameters with peer-reviewed claims: 0
- Parameters with textbook/reference claims: 5

Interpretation:

- The tier plan did move the run beyond extension-heavy evidence by normalized-claim volume: textbook/reference and international-institution sources each contributed more normalized claims than extension/public agronomy.
- The connector path did not yet deliver peer-reviewed trait claims. Crossref contributed mostly metadata-only captures, and the single OpenAlex open-text path yielded only two candidate claims and zero normalized claims.
- Google Books returned rate-limit errors during the wheat run, so textbook/reference evidence came from DuckDuckGo-discovered open sources rather than book connectors.
- Promotion is now a visible bottleneck: the only promoted durable claim was a broad Penn State Extension descriptive sentence, not a strong wheat physiological-trait claim.

Validation:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `PYTHONPATH=src .venv/bin/python -m compileall src tests`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-capability-map`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-handoff`

## 2026-06-04: Expanded Crop Physiology Parameter Catalog

- Expanded `config/parameters/core-crop-parameters.json` from 21 to 85 parameters and bumped the manifest version to `0.2.0`.
- Added broad farmer/researcher-relevant trait families beyond the original temperature/water/soil/planting pilot set:
  - canopy, photosynthesis, root, morphology, quality, harvest, stress, nutrient, phenology, thermal-time, soil, water, establishment, and management traits.
- Added wheat/cereal-relevant traits such as vernalization, photoperiod sensitivity, tillering duration, spike density, grains per spike, thousand kernel weight, grain protein, test weight, falling number, gluten strength, grain drydown, and preharvest sprouting risk.
- Added general crop physiological traits such as minimum/maximum growth temperature, photosynthesis optimum temperature, radiation use efficiency, light extinction coefficient, leaf area index, harvest index, rooting depth, root length density, crop coefficient, allowable depletion, water productivity, salinity threshold, nitrogen use efficiency, critical tissue nitrogen, lodging susceptibility, row spacing, target plant density, and tillage/residue response.
- Removed the `max_parameters: 12` cap and old family filter from the four `pilot-global-*.json` run configs so global comprehensive runs select all crop-applicable manifest parameters.

Expanded global query-plan counts:

| Run config | Planned queries |
| --- | ---: |
| `pilot-global-rice-001` | 425 |
| `pilot-global-sunflower-001` | 390 |
| `pilot-global-tomato-001` | 355 |
| `pilot-global-wheat-001` | 425 |

Current interpretation:

- `pilot-global-wheat-001` artifacts currently on disk still represent the earlier 60-query benchmark, not the new 425-query comprehensive plan.
- The manifest now covers a much broader trait ontology, but normalization/review/promotion rules still need to catch up for many new families; otherwise expanded searches will collect evidence that remains unmapped or low-specificity.
- Comprehensive live runs should be batched or rate-limited because 85 wheat parameters x 5 source tiers is a different operational scale than the prior 12-parameter pilot.

Validation:

- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli plan-queries --run-config config/runs/pilot-global-wheat.json`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-capability-map`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-handoff`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `PYTHONPATH=src .venv/bin/python -m compileall src tests`

## 2026-06-04: Expanded Global Wheat Rerun

- Reran `pilot-global-wheat-001` with the expanded 85-parameter catalog and five source tiers.
- Ran the full downstream pipeline after exploration: normalization, review, promotion, coverage, and PostgreSQL SQL export.
- Exported PostgreSQL SQL to `data/postgres/load-pilot-global-wheat-001.sql`; no `POSTGRES_DSN` was configured, so export mode was used.

Exploration summary:

- Queries executed: 425
- Parameters queried: 85
- Source tiers queried per parameter: 5
- Unique sources captured: 587
- Candidate claims: 2,646
- Search failures: 0
- Source failures: 467
- Access statuses: 390 open full text, 197 metadata only
- Discovery methods: 378 DuckDuckGo HTML, 197 Crossref, 12 OpenAlex

Source-tier movement:

| Source tier | Queries | Captured sources | Open full text | Metadata only | Candidate claims | Normalized claims | Promoted claims | Needs review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Extension/public agronomy | 85 | 62 | 62 | 0 | 359 | 345 | 1 | 188 |
| Industry/grower guides | 85 | 95 | 95 | 0 | 659 | 564 | 0 | 143 |
| International institutions | 85 | 108 | 108 | 0 | 805 | 708 | 0 | 290 |
| Peer-reviewed science | 85 | 209 | 12 | 197 | 30 | 0 | 0 | 0 |
| Textbooks/reference books | 85 | 113 | 113 | 0 | 793 | 721 | 0 | 458 |

Query family coverage:

| Family | Queries |
| --- | ---: |
| Temperature | 55 |
| Thermal time | 25 |
| Phenology | 35 |
| Canopy | 30 |
| Photosynthesis | 20 |
| Root | 15 |
| Water | 35 |
| Soil | 35 |
| Nutrients | 35 |
| Stress | 30 |
| Planting | 30 |
| Morphology | 25 |
| Quality | 20 |
| Harvest | 20 |
| Management | 15 |

Downstream outputs:

- Normalized claims: 2,338
- Manual-review claims: 1,079
- Seasonal-observation claims: 771
- Rejected claims: 473
- Promoted durable claims: 15
- Parameters requested: 85
- Parameters with normalized claims: 11
- Parameters with promoted claims: 1
- Parameters missing: 74
- Parameters needing review: 8
- Parameters with peer-reviewed claims: 0
- Parameters with textbook/reference claims: 6

Interpretation:

- The expanded query plan did execute all 85 wheat parameters across all five evidence tiers.
- The search/discovery layer now reaches far beyond the original 12-parameter pilot, but the current extractor/normalizer is still tuned mostly to temperature, planting windows, soil water, moisture, and a few unmapped generic buckets.
- Peer-reviewed discovery remains metadata-heavy: Crossref produced 197 metadata-only captures, OpenAlex produced 12 open-text captures, and the normalized peer-reviewed claim count remained zero.
- Google Books remained rate-limited on textbook/reference queries, so useful textbook/reference captures still came through DuckDuckGo fallback rather than book connectors.

Validation:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- `PYTHONPATH=src .venv/bin/python -m compileall src tests`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-capability-map`
- `PYTHONPATH=src .venv/bin/python -m crop_search_framework.cli write-handoff`

## 2026-06-22: Phase 1 — Generalized Crop Ontology + Extraction Contract

Implemented Phase 1 of the generalized farmer-complete crop ontology (plan: `.planning/generalized-crop-ontology-plan.md`, contract: `.planning/extraction-contract.md`). Paper/config + schema only — no LLM/API calls. All additions are additive and optional, so existing artifacts validate unchanged.

Schema changes (additive, backward-compatible):

- `schemas/parameter-manifest.schema.json`: added optional parameter fields `domain` (12 decision domains + economics), `parameter_kind` (`trait|operation`), `concept_scope` (`universal|crop_group|crop`), `decision`, `requires_stage_context`, `implementation_status` (`active|stub|deferred`).
- `schemas/normalized-claim.schema.json`: added optional `agronomic_scope {cultivar, management_system}`, `bbch_applicability {bbch_min, bbch_max, confidence, evidence_text}`, and `provenance.manifest_version`. `domain`/`parameter_kind` are intentionally NOT stored on claims — they are materialized from `parameter_id` + `manifest_version` at review/export.
- `schemas/crop-profile.schema.json`: added optional `bbch_stage_map` (many-to-many local-term ↔ BBCH range with confidence + source note) and `crop_parameters` overlay (for future crop-specific T2 params).
- `schemas/bbch-reference.schema.json`: new schema for the BBCH spine.

Data changes:

- `config/parameters/core-crop-parameters.json` migrated 0.2.0 → 0.3.0: all 85 existing parameters annotated with the new fields (derived from existing data, nothing fabricated), plus 7 `implementation_status: stub` placeholders so all 12 domains are visible (variety_cultivar ×2, crop_protection ×3, post_harvest_quality ×1, economics ×1). Result: 85 active + 7 stub = 92.
  - `parameter_kind`: `operation` where `category == management_recommendation` (24), else `trait` (68).
  - `concept_scope`: 61 universal, 24 crop_group, 0 crop.
  - `requires_stage_context`: 9 curated stage-varying concepts (crop coefficient, N timing, drought/heat/frost-sensitive stages, reproductive/grain-fill temperature) to keep `{stage}` query expansion bounded.
  - temperature stress thresholds (heat_stress, survival, reproductive_heat, grain_fill) routed to `stress_abiotic`; rest of temperature to `climate_site`.
- `config/reference/bbch.json`: BBCH principal-stage reference (0–9), shared across crops.
- `config/crops/*.json` (all 7): added `bbch_stage_map` mapping each crop's `growth_stage_terms` to BBCH ranges with per-entry confidence and notes (non-BBCH ops like tomato "transplanting" flagged low-confidence).

Code change (search touchpoint #1 only — the only safe pre-Phase-2 wiring):

- `src/crop_search_framework/parameters.py` `selected_parameters` now skips `implementation_status != active`, so stub/deferred parameters never generate queries. Verified: wheat plan selects 85 active params, 0 stubs, 0 crop_protection.

Artifacts:

- `.planning/coverage-matrix.md` (generated): domain × status/kind/stage matrix + concept-scope counts + stub gaps.
- `.planning/migrations/migrate_manifest_v030.py`, `.planning/migrations/add_bbch_stage_maps.py` (idempotent, reproducible).

Measured wheat baseline (for Phase 2 gates, `pilot-global-wheat-001`): 2,338 normalized claims; 16.1% unmapped; 57.2% in `temperature.optimum_growth_temperature`; 45.5% duplicate redundancy (1,064 redundant copies); max identical-sentence repeats 30; 14 active parameters with claims; 1 promoted.

Validation:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` (20 tests OK)
- `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`
- Schema-validated: manifest, bbch.json, all 7 crop profiles, 9 run configs, source tiers, and a 200-claim sample of existing normalized claims (still valid under the additive schema).
- `write-capability-map`, `write-handoff` regenerated.

NOT done (deliberately gated): Phase 2 (Claude extractor wiring, re-extraction of wheat, fresh-crawl spike) requires `ANTHROPIC_API_KEY` and incurs API cost; per the plan, expansion is gated on the Phase 2 wheat coverage table and needs explicit go-ahead. Search touchpoints #2 (`{stage}` expansion) and #3 (crop_parameters overlay union) are Phase 2 and validated only by the fresh-crawl spike (2b), since re-extraction reuses on-disk captures and does not re-run search.

## 2026-06-22: Phase 2 (start) — LLM extractor module

Built the extractor module with a pluggable backend so the pipeline is exercisable offline and the live path is the only thing waiting on an API key / spend approval.

- `src/crop_search_framework/llm_extract.py`:
  - `FixtureBackend` — deterministic, no network. Replays a recorded model response from `exploration/llm_cache/<run>/<capture>.json` when present; otherwise a small keyword stub. CI/offline default.
  - `ClaudeBackend` — live extraction via the Anthropic SDK (`claude-opus-4-8`), structured outputs (`output_config.format` json_schema with `parameter_id` enum-constrained to **active** manifest ids + `none`), prompt-cached manifest table, raw responses cached for replay/audit. `anthropic` imported lazily, so the module imports without it.
  - `build_output_schema` (strict; stubs are NOT valid targets), `validate_extraction_claims` (drops `none`/off-manifest, dedupes by `(parameter_id, evidence_text)`), and `extract_run` driver writing per-capture artifacts + a summary to `exploration/llm_extractions/<run>/`.
  - Output contract per `EXTRACTION_KEYS` aligns with `.planning/extraction-contract.md` (`cultivar`/`management_system` → claim `agronomic_scope`; `bbch_min`/`bbch_max` → claim `bbch_applicability`; populated by the normalizer in the next step).
- `src/crop_search_framework/cli.py`: new `extract-run <run_id> --backend fixture|llm` (default `fixture`; `--model`, `--manifest`).
- `pyproject.toml`: added optional `[llm]` extra (`anthropic>=0.40`).
- `tests/test_llm_extract.py`: 10 tests — schema enum/strictness, stub extraction + temperature-range + management_system parsing, determinism, cache replay, validation/dedup, hermetic `extract_run`, and lazy `ClaudeBackend` construction (no anthropic import at build).

Smoke (no API): `extract-run pilot-global-wheat-001 --backend fixture` ran over all 587 captures → 341 stub claims across 8 parameters; output removed afterward (the driver is covered hermetically by tests). This validates the module + driver + CLI on real captures without spend.

Validation: `unittest` 30 OK, `compileall` clean.

Next (still gated on key + approval): wire extractor output into `normalize.py` (materialize `domain`/`parameter_kind` from `manifest_version`, attach geo + `agronomic_scope` + `bbch_applicability`, dedup/merge), then run 2a (`--backend llm` re-extraction of wheat) and 2b (fresh-crawl spike). To run live: `pip install -e .[llm]`, set `ANTHROPIC_API_KEY`, `extract-run pilot-global-wheat-001 --backend llm`.

## 2026-06-22: Phase 2 (cont.) — Extractor wired into the normalizer

Wired extractor output into normalization so fixture/live extractions flow to schema-valid normalized claims of the new shape. Heuristic path unchanged (default); the LLM path is opt-in.

- `src/crop_search_framework/normalize.py`:
  - `_run_context` now also exposes `parameter_by_id`, `active_parameters`, and `manifest_version`.
  - `normalize_run_from_llm(run_id, backend, output_subdir="normalized")`: runs the backend per capture, maps each extraction → normalized claim, dedups/merges, validates, writes claims + a summary (raw_extracted_claims, normalized_claims, merged_away, conflicts).
  - `_claim_from_extraction`: reuses the existing geo inference (`infer_claim_geo_scope`/`infer_source_geo_scope`/`build_geo_evidence`) and time scope; materializes `attribute`/`attribute_subtype` from the manifest by `parameter_id`; stamps `provenance.manifest_version` and `extraction_method=llm:<backend>`; attaches `agronomic_scope` (cultivar/management_system) and `bbch_applicability` only when present. `domain`/`parameter_kind` are deliberately NOT stored (materialized later from `manifest_version`).
  - Dedup/merge on the combined applicability key (`entity`+`parameter_id`+`location_scope`+`time_scope`+`agronomic_scope`): exact-text dedup unions `provenance.source_urls`; overlapping numeric/range values merge into one record. `conflict_group_key` now includes `agronomic_scope` so different cultivars/management systems are not false conflicts (backward-compatible: existing claims have no `agronomic_scope` → unchanged grouping).
- `src/crop_search_framework/cli.py`: `normalize-run --from-llm [--backend fixture|llm] [--model] [--output-subdir]`.
- `tests/test_normalize_llm.py`: 8 tests — claim schema-validity with new fields, derivable fields absent, manifest-materialized subtype, text/no-stage omission, short-evidence drop, numeric merge + source union, management-system non-merge, text dedup, conflict-key includes agronomic scope.

Smoke (no API): `normalize-run pilot-global-wheat-001 --from-llm --backend fixture --output-subdir normalized_llm` → 341 raw extractions deduped/merged to 171 normalized claims (170 merged away, ~50% redundancy collapsed); all 171 schema-valid; `agronomic_scope` + `manifest_version` populated. Output written to a separate `normalized_llm/` dir to preserve the heuristic baseline, then removed (covered hermetically by tests).

Validation: `unittest` 37 OK, `compileall` clean.

Still gated on key + approval: live 2a (`normalize-run pilot-global-wheat-001 --from-llm --backend llm`, which re-extracts via Claude and writes to `normalized/` to become the new baseline) and 2b fresh-crawl spike. The full offline pipeline (extract → normalize new-shape → dedup/merge → schema-valid) is now demonstrable without spend.

## 2026-06-22: Phase 2 (cont.) — Local (free) extraction backend

Decision: avoid paid Anthropic API for extraction. Added a third backend so the live extraction path runs **free, locally**, with no change to the contract or downstream stages.

- `src/crop_search_framework/llm_extract.py`: `LocalBackend` (name `local`) calls an **Ollama** server (`$OLLAMA_HOST` or `http://localhost:11434`) via `requests` (existing dep, imported lazily). It reuses the same enum-constrained JSON schema (`build_output_schema`) as Ollama's structured-output `format`, the same system/user prompts, and the same `validate_extraction_claims`/cache. Default model `llama3.1` (override with `--model`). Connection failures raise an actionable error (start `ollama serve`, `ollama pull <model>`). `make_backend` now resolves `fixture | local | llm` and takes a `host`.
- `src/crop_search_framework/cli.py`: `extract-run` and `normalize-run --from-llm` accept `--backend {fixture,local,llm}` and `--host`. `--model` left at the Claude default auto-resolves to `llama3.1` for the local backend.
- `tests/test_llm_extract.py`: +3 tests (mocked Ollama HTTP) — parses structured response and drops `none`, posts to `/api/chat` with the enum-constrained `format` at temperature 0, surfaces an actionable error on connection failure, and `make_backend('local')` defaults to llama3.1 but respects overrides. 40 tests OK, compile clean.

Backends now: `fixture` (deterministic, no network — CI/offline default), `local` (Ollama, free, the chosen live path), `llm` (Claude, optional, only if someone sets a key). The Claude backend stays available but unused by default.

To run free local extraction: install Ollama (`brew install ollama`), `ollama serve`, `ollama pull llama3.1`, then `normalize-run pilot-global-wheat-001 --from-llm --backend local --output-subdir normalized_llm`. No API key, no spend. (Ollama is not currently installed on this machine.)

## 2026-06-23: Discovery — replace DuckDuckGo dependence with free per-tier APIs

Motivation: the only discovery path for three of five source tiers was the DuckDuckGo HTML scraper, which rate-limits/blocks and stalled runs. Goal: route every source tier to free, key-less APIs (incl. scientific journals and textbooks), keeping DuckDuckGo as a last-resort top-up only.

- `src/crop_search_framework/dev_tools/discovery_connectors.py`:
  - New connectors (all free, no API key): `europe_pmc_results`, `doaj_results` (science); `internet_archive_results`, `doab_results` (open-access books); `wikipedia_results` (general reference). Each returns the standard discovery-result shape via `discovery_result`, so no downstream change is needed.
  - `connector_results_for_tier` now covers **all five tiers** (previously only `peer_reviewed_science` and `textbook_reference`): science → OpenAlex+Crossref+Europe PMC+DOAJ; textbook → Google Books+Open Library+Internet Archive+DOAB; international_institution → OpenAlex+Wikipedia; extension_publication → OpenAlex+Internet Archive+Wikipedia; industry_grower_guide → OpenAlex+Wikipedia.
  - `relevance_gate` (`MIN_RELEVANCE_SCORE=4`, mirrors the DuckDuckGo gate) applied to the noisy connectors (Internet Archive, Wikipedia) so off-topic hits (e.g. "CIA Reading Room", newspapers) are dropped via the shared `score_source_result`. Topical scholarly APIs that match server-side stay ungated, like OpenAlex/Crossref.
  - Provider isolation unchanged: `gather_provider_results` records each provider exception in `provider_errors` while siblings still return — so a Google Books `429` no longer stalls the textbook tier.
- `config/mcp/servers.local.json`: `search-web` purpose string updated to describe the per-tier free-API routing and DuckDuckGo's demotion to last-resort top-up.
- `README.md`: documented the per-tier source routing, fail-soft behavior, and that DuckDuckGo is no longer load-bearing.
- `tests/test_discovery_connectors.py` (new): 15 tests — each new connector (open/metadata access detection, fulltext-link extraction, DC-metadata parsing, HTML-snippet stripping, relevance-gate noise drop), all-five-tiers-resolve routing, unknown-tier empty, and provider-error capture.

Live smoke (real APIs, query "wheat optimum growth temperature"): peer_reviewed 12 results across 4 providers / 0 errors; textbook 1 result (Google Books `429` captured, Internet Archive delivered); international/extension/industry 6–7 results each via OpenAlex+Wikipedia(+IA); 0 crashes; DuckDuckGo not invoked. Known limitation: the three non-scholarly tiers are currently scholarly-biased (OpenAlex/Wikipedia) — genuinely grower-facing/extension web content would need either curated `source_seeds` or a self-hosted SearXNG worker (deferred).

Validation: `unittest` 48 OK, `compileall` clean.

## 2026-06-23: Discovery — `seed_mode: augment` + curated grower/extension/institution wheat seeds

Follow-up to the per-tier API work: the API connectors made the three non-scholarly tiers (`international_institution`, `extension_publication`, `industry_grower_guide`) scholarly-biased (OpenAlex/Wikipedia). To put genuine grower-facing content back into those tiers, wired curated `source_seeds` into the global wheat run — but seeds previously only fired when live search returned *nothing*, which the new connectors rarely do.

- `src/crop_search_framework/exploration.py`:
  - New `seed_mode` run-config option (`fallback` | `augment`, default `fallback` → existing runs unchanged). In `augment`, matching seeds are merged alongside live search results instead of only-when-empty.
  - `_merge_results(search_results, seed_results)`: combines the two, de-duplicating by `source_url` with live search taking precedence (a seed whose URL already surfaced in search is dropped). Replaces the old `search_results + seed_results` concat.
- `schemas/exploration-run.schema.json`: added optional `seed_mode` enum.
- `config/runs/pilot-global-wheat.json`: `use_source_seeds: true`, `seed_mode: "augment"`, and 10 curated wheat seeds across the three tiers, each scoped to `source_tier_id` + specific `parameter_ids` (so a seed only fetches for the handful of params it covers, bounding fetch cost given the `(source_url, parameter_id)` dedup key). Extension: K-State handbook, SDSU, Nebraska G2122, Oregon State soil pH, Michigan State, Nebraska CropWatch. International: FAO crop-water + FAO irrigation, CIMMYT. Industry/grower: AHDB Wheat Growth Guide. Seeds target the gap families (planting, nutrients, water, phenology, harvest, stress) that pure search under-covers.
- `tests/test_exploration_seeds.py` (new): 6 tests — `_merge_results` dedup/precedence, config asserts augment mode, all three tiers seeded, seed fires for its tier+param, and does not fire for wrong tier or unlisted parameter.

Seed curation: every candidate URL HTTP-checked (dropped UMN/Yara/GRDC — bot-WAF 403/404 that would also block the fetcher), then live fetch+parse-verified end to end — FAO, CIMMYT, AHDB, K-State (PDF), Nebraska G2122 (PDF) each parsed to 8 candidate claims. Dropped Mosaic CropNutrition: returned undecodable binary (`document_type=other`, garbled text) through the basic fetcher; nutrients remain covered by the three extension seeds.

Known limitation unchanged: seeds are wheat-specific and curated for this pilot; broadening to other crops or to arbitrary grower web content would still want a self-hosted SearXNG worker (deferred).

Validation: `unittest` 54 OK, `compileall` clean; `pilot-global-wheat.json` validates against `exploration-run.schema.json`.

## 2026-06-23: Opus→vault pipeline — Phase 1 (durable corpus + renderer) + Phase 2 proof

Implements the v2 plan (`.planning/opus-vault-extraction-plan.md`): a durable, deduplicated raw layer ahead of Opus, plus the Obsidian renderer. Offline; no Opus-at-scale, no real vault writes yet.

- `src/crop_search_framework/corpus.py` (new): turns `(url, parameter_id)` captures into a content-addressed durable corpus.
  - `build_corpus(repo, run_id, source_run=)` — dedupes captures by **text hash** into a **document registry** (`documents/doc-*.json`: document_id, canonical url, doi, content_hash, text_hash, fetch/access metadata, parser_version), writes content-addressed text blobs, a **block store** (`blocks/doc-*.json`: sections/paragraphs/tables with anchors + offsets + labels via BeautifulSoup over the fetched HTML), **`query_hits.jsonl`** (document↔parameter/tier/query associations), and a `corpus_manifest.json` snapshot.
  - `corpus_qa(repo, run_id)` — emits `qa_report.{json,md}` with gates that decide readiness for Opus: capture_redundancy_collapsed (informational), duplicate_text_ratio (Opus input), metadata_only_share, background/Wikipedia share, table coverage, source-tier/domain counts, and a high-value retry queue.
- `src/crop_search_framework/vault_render.py` (new) + CLI `render-vault`: deterministic Obsidian rendering (Opus = judgment, code = rendering). One **data-point note** per crop×parameter (frontmatter facet `tags:` crop/domain/param/kind/source-tier/confidence/method/pest/stage + `[[wikilinks]]`, sourced-values table, evidence quotes), **entity hubs** (crop/parameter/domain/source/method/organism), and an **index MOC**. Vault safety: writes only under `--subdir` (default `DeCropsResearch/crop_science`), `generated_by: crop-search` marker guards overwrites (never clobbers foreign/hand-edited notes), `--dry-run`/`--prune`. Materializes `domain`/`parameter_kind` from the manifest (no denormalization onto claims).
- `src/crop_search_framework/cli.py`: new `build-corpus`, `corpus-qa`, `render-vault`.
- Tests: `tests/test_corpus.py` (5) + `tests/test_vault_render.py` (7) — text-hash dedup, query_hits linkage, QA metrics/gates, hashing; slugify, value summarization, confidence, note marker/tags/links, dry-run vs write, foreign-file no-clobber.

Ran on real data — `build-corpus pilot-global-wheat-001`: **825 captures → 341 unique documents** (280 with text), 825 query_hits. `corpus-qa`: capture_redundancy_collapsed **0.587** (the dedup win), duplicate_text_ratio **0.0** (Opus input), but **metadata_only 17.9% FAILS the <15% gate** and background (Wikipedia) 8.8% passes, tables present (79 docs) — i.e. the QA report correctly says the corpus needs full-text backfill before a full Opus pass (exactly the review's prediction). Gates_passed=False.

**Phase 2 proof (5 genuine Opus extractions over high-value docs):** acted as the Opus extractor on FAO physiology (base temp 0–4 °C, optimum 20–25 °C, germination 12–25 °C), K-State handbook (test weight 56 lb/bu, seeding rate 40–60 lb/acre), with CIMMYT/AHDB/UNL excerpts correctly yielding **no claims** (thin/boilerplate, no fabrication). Wrote them to the `llm_cache` the FixtureBackend replays → `normalize-run --from-llm` (5 claims) → `render-vault` to a temp dir → clean, correctly-tagged notes across **temperature, planting, quality** domains with real provenance (vs the heuristic's `0–500 °C` garbage). Proof artifacts removed; the chain is covered hermetically by tests. Notable finding: the UNL "G2122" seed (mapped to `nutrients.nitrogen_requirement`) is actually a soil-temperature planting guide — Opus reading the real text corrects the seed's query-label assumption.

Next (gated): Phase 1.5 wheat corpus backfill (refetch 429/5xx, OA full text for the 17.9% metadata-only, drop low-value Wikipedia) to pass the QA gate; then Phase 3 full-wheat Opus over the ~280 unique docs → normalize → `render-vault` into the real vault.

## 2026-06-23: Phase 1.5 — corpus backfill (OA full text + junk exclusion + provider hardening)

Built the crawl-side hardening the QA gate demanded and ran it on wheat; the corpus now passes the gate.

- `src/crop_search_framework/backfill.py` (new):
  - `http_get_with_retry` — exponential backoff on 429/5xx + an on-disk provider cache (the retry/backoff/cache the review asked for).
  - `resolve_oa(doi)` — open-access full-text resolution via Unpaywall, then OpenAlex `best_oa_location`.
  - `is_junk_doi` — flags non-article DOIs (supplements `/supp` `/table-` `/fig-`, peer-review stubs `/reviews/` `/submission`, F1000 `10.3410/`, GeoDB datasets `10.3974/`).
  - `backfill_corpus(repo, run_id, email)` — for each metadata-only document: junk/no-DOI → `excluded_from_opus`; else resolve OA and fetch+parse full text (reusing the `fetch-web`/`parse-document` tools, which handle HTML+PDF), updating the document record + block store + content-addressed blob in place and flipping `access_status` to `open_full_text`; writes `backfill_report.json`.
- `src/crop_search_framework/corpus.py`: `corpus_qa` now computes its shares/gates over the **Opus input set** = documents not `excluded_from_opus`, and reports `all_document_count`, `opus_input_document_count`, `excluded_from_opus_count`, `backfilled_full_text_count`.
- `src/crop_search_framework/cli.py`: new `backfill-corpus <run> [--email] [--limit]`.
- `tests/test_backfill.py` (7): junk-DOI detection, backoff-then-succeed + give-up, Unpaywall-preferred OA resolution, and an end-to-end backfill (mocked resolver + fetch tools) asserting junk exclusion, full-text recovery, and the QA recount.

Live run on wheat: 61 metadata-only candidates → **43 excluded as junk DOIs** (70% — the Crossref connector had surfaced mostly supplements/reviews/datasets), 9 excluded (no OA), 9 resolved → **4 fetched to full text**, 5 fetch-failed (publisher blocks). Result: Opus input set **289 docs** (52 excluded from 341), **metadata-only share 0.179 → 0.017**, background 0.104, duplicate-text 0.0, 80 docs with tables → **all gates pass, GATES_PASSED=True**. The corpus is now ready for the full Opus extraction pass.

Follow-up still open: wire `http_get_with_retry` into the live discovery connectors (currently only the backfill/OA path uses it), the live discovery ledger + ranked-fetch-queue, and the 5 fetch-failed OA docs (publisher-specific fetch). Then Phase 3 full-wheat Opus → `render-vault` into the vault.

Validation: `unittest` 73 OK, `compileall` clean.

Validation: `unittest` 66 OK, `compileall` clean.

## 2026-06-23: Phase 3 — full-wheat Opus extraction → vault (the payoff)

Ran the full Opus extraction over the gate-passed corpus and rendered the result into the user's Obsidian vault.

- Materialized an Opus run (`pilot-global-wheat-opus`) from the 284 text-bearing, non-excluded corpus documents: one synthetic raw capture per unique document (id = document_id), so the existing `normalize-run --from-llm --backend fixture` machinery (geo inference, materialization, dedup/merge, schema validation) applies unchanged. Generated `exploration/corpus/param_vocab.txt` (84 active parameter ids) and 16 batch manifests.
- **Opus extraction via 16 subagents** (Task tool, ~18 docs each), each reading the document text + block-store tables and writing manifest-constrained claims to `exploration/llm_cache/pilot-global-wheat-opus/<document_id>.json`. Result: **1,273 claims across 284 docs**, 0 invalid parameter_ids; agents correctly returned empty for off-crop/boilerplate/blocked docs (no fabrication) and flagged the FAO doc mislabeled "Tobacco" + several unit edge cases.
- Normalize: 1,257 raw → **773 normalized claims** (484 merged/deduped), 54 conflict groups. Render: **85 data-point notes + 96 entity hubs + 160 source notes = 342 files** written into `Tino_deCrops/DeCropsResearch/crop_science/` (dry-run first, 0 foreign-file collisions, marker-guarded).
- **Acceptance gates smashed** vs the heuristic baseline (12 params, 63% temperature): parameters with notes **85/85** (gate ≥40); temperature share **6.3%** (gate <25%); families covered **15** (gate ≥8: nutrients 137, stress 94, phenology 79, planting 72, management 51, temperature 49, water 48, morphology 48, canopy 41, harvest 40, soil 35, quality 28, photosynthesis 20, thermal_time 17, root 14).

Note: `coverage-run` requires a `config/runs/<run_id>.json` and was skipped for the synthetic Opus run (gates computed directly). The synthetic raw run + cache + normalized claims are retained for reproducible re-render. Follow-ups: the live discovery ledger / ranked-fetch-queue / connector-wide retry, retry the 5 publisher-blocked OA docs, a Crossref `type==journal-article` filter at discovery (70% of metadata-only DOIs were non-articles), and Phase 5 (other crops).

Validation: `unittest` 73 OK, `compileall` clean; vault render verified (342 .md, 85 `Wheat — *` data-point notes).

## 2026-06-24: Planning — parameter ontology widening

Created `.planning/parameter-ontology-expansion-plan.md` to turn the parameter-list widening discussion into an actionable, compatibility-aware plan. The plan keeps broad authoring and narrow activation separate: new concepts enter as `stub`/`deferred`, then graduate to `active` only after query preview, extraction-contract checks, and retrieval/extraction eval.

Compatibility conclusion: the rework is non-breaking if staged additively. Stubs/deferred entries are skipped by `selected_parameters()` and excluded from the LLM extractor enum; active additions intentionally expand query counts, extraction vocabulary, and coverage denominators, so they require manifest-version bumps plus test/query/eval updates. No code, config, manifest, or generated extraction artifacts were changed in this planning step.

Updated the plan after review to make two compatibility constraints explicit before execution:

- Manifest metadata is schema-first, not a drop-in. `parameter-manifest.schema.json` is closed with `additionalProperties: false`, so optional metadata such as `query_units` or `expected_value_shape` requires a schema property update before any manifest record can use it.
- Extraction-contract optionality does not exist yet. `EXTRACTION_KEYS` is currently a fixed required tuple and the model output schema is closed, so Phase 4 now requires a validator/schema rework for optional extraction fields before adding `organisms`, `method`, economics fields, or block anchors.

## 2026-06-24: Parameter ontology expansion foundation

Implemented the safe, non-active portions of `.planning/parameter-ontology-expansion-plan.md`.

Manifest/schema:

- Extended `schemas/parameter-manifest.schema.json` with optional authoring metadata: `query_units`, `query_terms`, `stage_terms`, `expected_value_shape`, `expected_scope`, `false_positive_terms`, and `extraction_notes`.
- Bumped `config/parameters/core-crop-parameters.json` to `manifest_version: 0.4.0`.
- Added 34 non-active candidate parameters across variety/cultivar, crop protection, post-harvest/storage, economics, and management deepening. New catalog shape: **126 total = 85 active + 34 stub + 7 deferred**.
- Kept the active set unchanged, so global query counts are unchanged: wheat 425, rice 425, sunflower 390, tomato 355.
- Added parameter-level query metadata to selected active parameters (`nutrients.nitrogen_requirement`, `water.crop_coefficient`, `harvest.harvest_moisture`) and expanded domain defaults in `config/query-templates/default.json`.
- Refreshed `.planning/coverage-matrix.md` for the 0.4.0 catalog.

Extraction/normalization:

- Split the extractor contract into `REQUIRED_EXTRACTION_KEYS` and `OPTIONAL_EXTRACTION_KEYS`.
- Kept the model output schema closed, but now only core keys are required; optional fields are schema-declared.
- `validate_extraction_claims()` now accepts old cached outputs missing optional fields and backfills optional extension keys to `None`.
- Added optional fields for organisms, method, economics context, and block/table provenance.
- `_claim_from_extraction()` carries non-empty optional extraction context into normalized claim provenance; `schemas/normalized-claim.schema.json` now allows those provenance fields.
- Fixed `eval_harness.py` for Python 3.8 by replacing `Path.is_relative_to` with a compatibility helper.

Tests/validation:

- Added/updated tests for manifest authoring metadata, optional extraction schema behavior, old-cache compatibility, optional-field preservation, normalized provenance carry-through, and the Python 3.8 eval-harness path helper.
- `PYTHONPATH=src python3 -m crop_search_framework.cli validate parameter-manifest.schema.json config/parameters/core-crop-parameters.json`
- `PYTHONPATH=src python3 -m unittest discover -s tests` → 110 tests OK.
- `PYTHONPATH=src python3 -m compileall src tests` → clean.

Intentionally not done: no new parameters were promoted to `active`. The first active batch still needs retrieval/extraction gold cases and a small live query spike before query volume and coverage denominators are expanded.

## 2026-06-24: Planning — crop relationship matrix

Created `.planning/crop-relationship-matrix-plan.md` for crop-to-crop compatibility, rotation, intercropping, companion cropping, double cropping, relay/strip/mixed cropping, and cover-crop relationships.

Key design decision: represent all crop relationships as a dense coverage matrix backed by sparse evidence claims. Every configured crop pair gets a matrix cell, including self-pairs for continuous cropping, but unknown pairs stay explicitly `not_searched` or `searched_no_evidence` instead of being inferred as neutral, good, or bad.

The plan keeps the existing crop-rotation approach as the high-level temporal implementation of the matrix:

- `management.rotation_recommendation` remains the broad active rotation summary.
- `management.rotation_interval`, `management.cover_crop_compatibility`, and `management.double_crop_window` become bridge concepts into pair-specific matrix cells.
- Pair-specific rotation evidence is modeled with subject crop, object crop, direction, relationship mode, effect, mechanisms, context, and provenance.

Compatibility posture: non-breaking if relationship work is implemented as a separate staged path with schema-first artifacts, disabled-by-default pair-aware query planning, separate relationship extraction outputs, and unchanged existing normalized claim/entity semantics.

Reviewed and tightened the relationship matrix plan after design feedback while preserving the full `N * N` matrix:

- Reframed the current crop universe as 7 configured crop profiles, so the full ordered matrix is 49 crop-id cells and complete rotation coverage is practical now.
- Made the dense matrix strictly `crop_id x crop_id`; crop-group relationship evidence now belongs in a separate rollup layer rather than extra matrix cells.
- Added mode directionality and keying rules: every matrix cell keeps an `ordered_pair_key`, while symmetric modes deduplicate evidence under a sorted `canonical_relationship_key` and mirror summaries into both ordered cells.
- Added a routing boundary so named-pair rotation evidence goes to the relationship extractor, broad unnamed rotation advice stays in `management.rotation_recommendation`, and the same evidence span is not emitted into both paths.
- Specified that the future relationship validator must treat optional fields as genuinely optional from day one.

## 2026-06-24: Crop relationship matrix foundation

Implemented the non-breaking foundation of `.planning/crop-relationship-matrix-plan.md`.

Config/schema:

- Added `config/relationships/relationship-vocabulary.json` with nine relationship modes: rotation, continuous cropping, double cropping, intercropping, relay cropping, strip cropping, mixed cropping, companion cropping, and cover-crop fit.
- Added closed JSON schemas for relationship vocabulary, dense matrix skeletons, relationship query plans, and future relationship claims.
- Relationship claim schema keeps optional fields genuinely optional from day one, so future validators should not inherit the old exact-key extraction-contract problem.

Code/CLI:

- Added `src/crop_search_framework/relationships.py` for crop-universe loading, ordered `crop_id x crop_id` pair generation, full matrix skeleton creation, mode directionality, canonical relationship keys, and source-tier-aware relationship query rendering.
- Added `crop-framework write-relationship-matrix`.
- Added `crop-framework plan-relationship-queries`.
- Added `crop-framework discover-relationships`, which executes pair-aware searches into `exploration/relationships/discovery/<run_id>/results.jsonl`.
- Relationship discovery rows preserve `subject_crop_id`, `object_crop_id`, `relationship_mode`, `relationship_subtype`, `ordered_pair_key`, `canonical_relationship_key`, and `relationship_source_key`.
- Extended `schemas/raw-capture.schema.json` with optional relationship context fields so future relationship fetch captures can carry pair metadata without breaking existing single-crop captures.
- Updated the capability map generator and README to expose the relationship subsystem.

Generated artifacts:

- `exploration/relationships/matrix/current.json`: 7 crops, 49 ordered cells, all initialized to `not_searched`; all nine relationship modes are present under each cell.
- `exploration/relationships/query_plans/rotation-current.json`: complete current-universe rotation query plan with 49 ordered pairs × 1 query per pair × 5 source tiers = 245 queries.
- Symmetric mode keys are canonicalized without shrinking the matrix: for example, soybean/corn intercropping stores evidence under `intercrop|corn|soybean` while the ordered cell remains `soybean|corn`.

Validation:

- `validate crop-relationship-vocabulary.schema.json config/relationships/relationship-vocabulary.json`
- `validate crop-relationship-matrix.schema.json exploration/relationships/matrix/current.json`
- `validate crop-relationship-query-plan.schema.json exploration/relationships/query_plans/rotation-current.json`
- `validate raw-capture.schema.json exploration/raw/pilot-global-wheat-001/pilot-global-wheat-001-capture-451.json`
- `tests/test_relationships.py` covers the 49-cell matrix, crop-group rollup exclusion, symmetric canonical keys, rotation query planning, optional claim fields, and relationship discovery ledger context.

Still pending:

- Relationship fetch execution.
- Relationship extraction from documents.
- Relationship claim normalization/review/promotion.
- Populating matrix cells from evidence and rendering farmer-facing relationship views.

## 2026-06-29: Hybrid relationship evidence graph

Implemented the hybrid relationship lane from `.planning/hybrid-relationship-evidence-graph-plan.md`
and `.planning/hybrid-graph-implementation-plan.md`: a coarser evidence graph
for minor crops and aggregate nodes layered on top of the existing dense
crop-pair matrix, plus a request-time resolver. The dense `crop_id x crop_id`
matrix lane is unchanged; old-style crop-only claims stay valid.

Schema:

- `schemas/crop-relationship-claim.schema.json`: added optional
  `subject_node_type/id` and `object_node_type/id` (node types: crop, genus,
  botanical_family, functional_group, host_group). Replaced the unconditional
  crop-field requirement with `allOf` of two per-side `anyOf` blocks, so each
  side must carry *either* crop fields *or* node type+id — aggregate (family /
  host-group) claims validate, but an identity-less claim is still rejected.
- `schemas/crop-relationship-query-plan.schema.json`: added top-level
  `pair_mode` plus item-level `pair_mode`, `search_pair_key`, and
  `candidate_ordered_pair_keys` (the query item is `additionalProperties:false`,
  so these had to be declared).

Planner (`relationships.py`):

- Added `unordered_crop_pairs` (n(n+1)/2 with self-pairs) and a `pair_mode`
  option to `build_relationship_query_plan` / `discover_relationships`.
- Unordered planning over directional modes (e.g. rotation) renders a
  *direction-neutral* template and carries `search_pair_key` (`mode|min|max`)
  plus `candidate_ordered_pair_keys` (both orderings), so one neutral search can
  feed both ordered cells without losing reverse-direction intent. Discovery
  rows, dedup keys, and the discovery summary all thread `pair_mode`.

Graph + resolver (`relationship_pipeline.py`):

- `load_node_catalog` loads `config/relationships/node-catalog.json` (crop,
  genus, botanical_family, functional_group, host_group nodes).
- `build_relationship_graph` indexes evidence-bearing claims keyed by
  `(relationship_mode, subject_tuple, object_tuple)` so modes never bleed into
  each other. A normalization adapter synthesizes `("crop", crop_id)` tuples for
  legacy crop-only claims, and a status filter keeps only `accepted` /
  `needs_review` (never `rejected` / `conflict`). Persisted to
  `exploration/relationships/graph/<run_id>.json`.
- `resolve_crop_relationship(run_id, subject, object, mode="rotation")` resolves
  aliases via the catalog, then: exact crop evidence → cross-aggregate group
  inference (botanical_family → functional_group → genus, in relationship
  direction; catches both same-family avoidance and cereal-after-legume) →
  host-risk overlay for host groups shared by both crops. Unknown aliases return
  `no_evidence` with `unknown_nodes`.
- `relationship_parameter_span_conflicts` flags any evidence span emitted as both
  a relationship claim and a `management.rotation_recommendation` parameter
  claim (the cross-lane routing guard).

CLI (`cli.py`):

- Added `--pair-mode {ordered,unordered}` to the existing
  `plan-relationship-queries` and `discover-relationships` (not duplicated).
- Added `build-relationship-graph <run_id>` and `resolve-crop-relationship
  <run_id> --subject <crop_or_alias> --object <crop_or_alias> [--mode rotation]`.

Tests (`tests/test_hybrid_relationship_graph.py`, 11 tests):

- Node catalog validity; crop claims valid with and without node fields; ordered
  matrix population from unordered search context; unordered pair counts
  (7→28, 25→325, 120→7260); host-risk caveat overlay on direct evidence; family
  inference; cross-group functional-group inference (cereal-after-legume) and
  family-over-functional-group priority; unknown-pair `no_evidence`; rejected
  claims excluded from the resolver; cross-lane span-conflict detection.

Still pending (unchanged manual gates):

- Live relationship fetch + in-session Opus extraction, review, and human
  acceptance.
- Directional-evidence assignment from neutral unordered sources is exercised by
  counts only; add an extraction-level test when the Opus extraction lane lands.
- Per-mode resolver output (resolve across all nine vocabulary modes at once).

## 2026-06-29: Aggregate relationship discovery (production side)

Implemented `.planning/aggregate-relationship-discovery-plan.md`. The hybrid
graph could *consume* aggregate (family/functional-group/host-group) evidence
but the live pipeline only ever *searched crop pairs*, so the inference lane was
structurally unfeedable — proven by a live run whose graph held a real
`rotation|corn|soybean` direct claim but an empty aggregate index. This adds the
missing production side: group-level discovery that can actually surface and
extract aggregate evidence.

Schema:

- `crop-relationship-query-plan.schema.json`: added top-level `node_mode` and,
  on `query_item`, optional node fields + `subject_search_label`/
  `object_search_label`, with per-side `anyOf` (crop fields OR node type+id) so
  node-only aggregate items validate.
- `crop-relationship-vocabulary.schema.json`: added optional
  `aggregate_query_templates` per mode.
- `raw-capture.schema.json`: added node fields so aggregate identity survives
  into fetch captures.

Vocabulary:

- Added `aggregate_query_templates` (group-level `{subject_group}`/
  `{object_group}` placeholders) to rotation, continuous_cropping, intercrop,
  companion_crop, and cover_crop, including a host-risk template.

Planner / discovery (`relationships.py`):

- `AggregateNode` + `load_aggregate_nodes` (family/functional-group/host-group),
  `aggregate_node_pairs` (host groups pair only with themselves), node-aware
  canonical/search keys, and group-template rendering.
- `build_relationship_query_plan` / `discover_relationships` gained
  `node_mode="crop"|"aggregate"`. Aggregate runs default to principle-bearing
  tiers (textbook/institution/extension) and emit node identity +
  mode-agnostic `subject_search_label`/`object_search_label` on every item/row.
- Plumbing cautions fixed: the connector call now uses the mode-agnostic search
  label (not `subject_crop_label`), and crop-id readers were made `.get()`-safe.

Fetch queue (`relationship_pipeline.py`):

- `select_relationship_fetch` no longer assumes crop ids; it reads them via
  `.get()` and carries `subject_node_type/id` + `object_node_type/id` +
  `node_mode` onto candidate and queue rows. Captures and corpus
  `relationship_hits` carry the node fields too.

Prompt:

- `prompts/relationships/extract-opus.md` gained an explicit level-of-evidence
  rule: crop-specific sentence → direct crop claim; group-level sentence →
  aggregate claim; never generalize or narrow; extract both from one document
  when both are genuinely present.

CLI:

- `--node-mode {crop,aggregate}` added to `plan-relationship-queries` and
  `discover-relationships` (threaded through summaries and saved plans).

Tests (`tests/test_relationship_aggregate_discovery.py`, 5 tests):

- Aggregate pair counts (ordered 74 / unordered 44); group-term rendering with
  no crop id; crop-mode items still carry crop labels; **fetch-queue survival**
  (aggregate ledger with no crop ids selects without `KeyError`, node ids
  retained); and end-to-end production→consumption (a `cereal ← legume`
  functional-group claim makes `resolve("wheat","soybean")` return
  `inferred_from_group` / `functional_group`).

Verified live: `discover-relationships --node-mode aggregate` ran real
group-level searches (rows carry `botanical_family`/`functional_group` identity,
no crop ids), and `select-relationship-fetch` queued them without error. The full
suite is 153 tests, green.

Still pending (unchanged manual gates):

- Live aggregate fetch + in-session Opus extraction of aggregate claims, then
  human acceptance.
- Genus-level aggregate queries; rolling up direct crop claims into aggregate
  summaries (inference-over-evidence, separate provenance rules).

## 2026-06-29: Intercropping / symmetric relationship capture

Implemented `.planning/intercropping-relationship-plan.md`. Intercropping had a
labeled drawer (vocabulary modes + matrix cells) but no contents, plus two
correctness gaps that would make symmetric evidence unreliable even once filled.
`intercrop`, `strip_crop`, `mixed_crop`, and `companion_crop` are symmetric;
`relay_crop` is directional and is **not** mirrored.

Two gaps closed:

- **Gap A (graph/resolver used a directed key):** an `intercrop corn|soybean`
  claim was invisible to `resolve("soybean","corn")`.
- **Gap B (matrix trusted the claim's key string):** a reverse-emitted
  `intercrop|soybean|corn` missed both sorted matrix cells.

Both are fixed in code, not just the prompt:

- `relationships.py`: `canonicalize_endpoints(directionality, a, b)` (sorted for
  symmetric) + `mode_directionality_map`.
- `relationship_pipeline.py`: `normalize_symmetric_claims` runs inside
  `validate_relationship_claims`, so both matrix population and the graph read
  claims with canonical (sorted) endpoints regardless of extractor output. The
  graph persists a `mode_directionality` map; `resolve_crop_relationship` sorts
  symmetric `(subject,object)` (direct and aggregate) before lookup. Directional
  modes are untouched.

Other changes:

- `--pair-mode auto` (new default on both relationship CLI commands) resolves to
  `unordered` when every selected mode is symmetric, else `ordered`.
- Added `aggregate_query_templates` to `strip_crop` and `mixed_crop` so the whole
  symmetric family supports `--node-mode aggregate` (it previously covered only
  `intercrop`/`companion_crop`).
- `prompts/relationships/extract-opus.md`: intercrop section — subtypes,
  single-`effect` decision rule (e.g. `beneficial` for measured LER > 1,
  `compatible` for non-quantified), context fields (arrangement/row_ratio/
  density), and the explicit-crop-pair routing rule.
- `relationship_parameter_span_conflicts` now accepts a single id or a set of
  parameter ids (forward-looking for an intercropping parameter; none exists in
  the manifest today).
- Added `tests/golden/relationships/intercrop.json` (both ordered cells) so
  `eval_relationships` scores the intercrop lane; updated the existing eval test
  to populate both modes (4 gold records, full recall).

Tests (`tests/test_relationship_intercrop.py`, 7 tests): symmetric resolve both
orderings (direct + aggregate); rotation and relay_crop NOT mirrored; **matrix
mirroring from a reverse-keyed claim** (Gap B guard); `--pair-mode auto`
resolution; span-guard with a supplied parameter id. Full suite 160 tests, green;
intercrop discovery live-smoked (auto → unordered, symmetric rows).

Still pending (unchanged manual gates):

- Live intercrop fetch + in-session Opus extraction of intercrop claims, then
  human acceptance.
- Deeper relay/strip/mixed extraction nuance; quantitative LER synthesis across
  sources.

## 2026-06-30: EU crops + fetch robustness + crop reference articles

Ran the relationship pipeline on Europe's most important annual crops and, where
a bounded crawl failed to capture some, fixed the two real pipeline gaps that
caused it.

Crops / catalog:

- Added EU crop profiles `config/crops/{barley,rapeseed,sugar_beet,potato}.json`
  and matching `major_direct` node-catalog entries, with real host links
  (rapeseed -> clubroot_host, potato -> solanaceae_host). Universe is now 11
  crops; the universe-count tests were made data-driven (derive n from
  config/crops) so they don't re-break as crops are added.
- Ran `europe-rotation-001` (rotation, Europe-scoped, bounded): 380 discovery
  rows -> 14 docs -> 8 in-session claims. Captures barley, rapeseed, wheat,
  potato, sugar_beet, sunflower (6/7) plus a working clubroot host-risk overlay
  (rapeseed x brassica). Corn's evidence lives in the earlier breadth run.

Fetch robustness (`dev_tools/fetch_web.py`, `relationship_pipeline.py`):

- `infer_document_type` now recognizes `/pdf` endpoints with query strings
  (e.g. MDPI `.../pdf?version=...`), not just `.pdf` suffixes.
- `_fetch_one` salvages PDFs that are actually HTML challenge/error pages
  (parses them as HTML), and rejects low-value parses — empty text or
  bot-challenge pages ("checking your browser", "reCAPTCHA", "just a moment",
  etc., under a length threshold) — so they are recorded as failures, not
  silently counted as full-text documents. A real long article that merely
  mentions "captcha" is not dropped.

Crop reference articles (`relationship_pipeline.py`, `cli.py`):

- New `fetch-crop-references <run_id> [--crop ...] [--crop-dir]`:
  `crop_reference_url` + `fetch_crop_references` fetch each crop's main Wikipedia
  article (redirects resolve synonyms: Corn -> Maize, Oilseed rape -> Rapeseed)
  into the relationship raw layer, bypassing hostile-publisher PDFs and noisy
  pair-template search. These parse cleanly as HTML and carry rotation prose. The
  reference path is what captured sugar beet (3-year rotation with grain) and
  sunflower (rotation with cereals / soybean / rapeseed) after three discovery
  attempts failed on those crops.

Tests (`tests/test_relationship_fetch_robustness.py`, 7 tests): `/pdf`-endpoint
detection; challenge/empty vs real-article low-value classification; HTML sniff;
`crop_reference_url` (incl. synonym labels). Full suite 167 tests, green.

Pipeline lesson recorded: crop-pair template search over science tiers yields
crop-specific or off-topic primary research; rotation-role knowledge for
well-known crops lives in encyclopedia/extension references, so the reference
path is the reliable way to seed it.

## 2026-06-30: Tier-aware evidence layer (Part A)

Made the relationship evidence layer source-tier aware so the methodology "use
textbook references as the basis, replace with peer-reviewed wherever possible"
is expressible and measurable. The fetch side was already tier-aware; matrix
population and the resolver were tier-blind (plain effect majority vote).

- `config/source-tiers/default.json`: defined `reference_encyclopedia` (priority
  6) for ranking only; kept it OUT of the default discovery `tier_order` so it is
  never silently planned. Added a Wikipedia-only `reference_encyclopedia` branch
  to `connector_results_for_tier` so the opt-in tier is not a dead lane.
- `source_tiers.py`: `tier_rank_index` / `tier_rank` / `tier_band` + an
  `EVIDENCE_BAND_TIERS` (peer-reviewed) vs reference-backbone split.
- `relationship_pipeline.tiered_effect`: the DECIDING tier (best tier carrying a
  usable effect) sets `summary_effect`, `best_source_tier`, and `evidence_grade`;
  an all-`unknown` top tier no longer masks a lower tier's real effect; a
  non-deciding claim whose decisive polarity is overridden raises
  `tier_superseded_conflict` (even under a `conditional`/`unknown` summary). Full
  effect-polarity table (positive/negative/conditional/neutral/ignore).
- Applied in `populate_relationship_matrix` (new cell fields + schema) and the
  resolver. `source_tier_id` is now schema-required and validated against the
  manifest (unknown tier dropped). Centralized claim dedup by
  `relationship_claim_id` in `_graph_from_claims`.
- Cross-run merged graph (`build_merged_relationship_graph`) + new
  `relationship-coverage-report` CLI: reports answerable pairs/55 per mode,
  evidence-grade split, accepted-vs-provisional (review status), directed
  upgrade-candidate keys, and `unknown_crops`. `--aggregate-node-type` filter
  added so aggregate runs can scope to `functional_group` (the lean backbone).

Two adversarial review rounds (independent agent + probes) found and fixed real
bugs in the first cut: effect-masking under an unknown top tier, the supersede
flag not firing under a non-committal summary, grade decoupled from the deciding
tier, directional upgrade candidates collapsing to one direction, and a
duplicate-id phantom conflict. Tests: `tests/test_relationship_tiered_evidence.py`
(+ fixtures in the existing relationship suites). Full suite 192 tests, green.

## 2026-06-30: Functional-group reference backbone (Part B)

Built a grounded group-level rotation/intercrop backbone so the resolver answers
many crop pairs from a few cited group claims (never inventing facts — every
claim cites a verbatim passage).

- Runs `backbone-rotation-001` / `backbone-intercrop-001`: aggregate discovery
  scoped to `functional_group` + backbone tiers -> select-fetch -> fetch ->
  corpus. Free-web aggregate discovery proved noisy (off-target tropical/climate
  papers); the solid grounding came from authoritative overview articles
  (Crop rotation, Rapeseed, Green manure, Monoculture, Cereal).
- 8 grounded group/family claims: cereal-after-legume (beneficial, N), cereal<->
  oilseed (compatible, rapeseed break crop), legume<->cereal (compatible),
  cereal-after-cereal (conditional, same-family/monoculture), oilseed-after-
  oilseed (avoid, sclerotinia self-rotation), solanaceae-after-solanaceae
  (conditional, family inference -> potato/tomato), cereal x legume intercrop
  (beneficial, N fixation).
- Human/AI review pass: promoted 23 sound `needs_review` claims (8 backbone + 15
  prior peer-reviewed/reference) to `accepted`.

Coverage after review (across all 5 relationship runs):

| Mode | Accepted answerable / 55 | peer_reviewed | reference_backbone |
| --- | ---: | ---: | ---: |
| rotation | 25 | 11 | 14 |
| intercrop | 4 | 4 | 0 |

32 directed rotation upgrade-candidate keys identified for Part C (peer-reviewed
replacement of reference-grade pairs). Honest remaining gaps: root-crop, cotton,
and fruiting-vegetable group pairs (rotation) and all intercrop beyond
cereal-legume — left as `none`, not fabricated, pending better sources.

## 2026-06-30: Peer-reviewed upgrades (Part C, first pass)

Started the peer-reviewed upgrade lane against the reference-backbone upgrade
candidates. Targeted search (not the noisy aggregate pipeline) found open-access
field studies; claims cite verbatim quantitative results.

- `upgrade-rotation-001`: 2 peer-reviewed group claims from one Frontiers field
  trial (10.3389/fpls.2023.1265994, Central Germany 2020-2022) -- cereal-after-
  cereal yield penalty (continuous/2nd wheat ~5.4-6.1 vs ~7.1 Mg/ha after a break
  crop) and cereal-after-oilseed break-crop benefit (~1.7 Mg/ha higher wheat after
  oilseed rape). Reviewed and accepted.
- Effect: rotation accepted grade split moved 11->20 peer-reviewed / 23->14
  reference-backbone (9 pairs upgraded, mainly the 6-pair cereal-cereal cluster).
  Total answerable unchanged (upgrades raise grade, not coverage). The tier layer
  superseded the reference claims with no conflict (same polarity).
- Sugar-beet-before-wheat and the rotation meta-analysis were paywalled
  (tandfonline 403, Nature redirect), so root-crop and broader cereal-legume
  upgrades remain pending an open-access source. 36 directed upgrade candidates
  remain.

### Part C continuation (same day)

Added two more open-access peer-reviewed upgrades: oilseed-after-oilseed
(PLOS ONE PMC3613410 -- up to 25% yield decline in continuous OSR) and
cereal-after-legume (Lukavec long-term trial 1979-2018, PMC10974760 -- winter
wheat 5.4 t/ha after legumes vs 4.1 t/ha after cereals). Four peer-reviewed
upgrade claims total.

Net Part C effect: rotation accepted grade split moved 11->23 peer-reviewed /
23->11 reference-backbone (12 pairs upgraded); total answerable unchanged at
34/55. The remaining 11 reference-backbone pairs are almost all root-crop
(potato / sugar_beet); the wheat-after-beet yield numbers sit behind paywalls
(tandfonline 403, MDPI blocks WebFetch), so they stay at reference grade pending
an accessible source.
