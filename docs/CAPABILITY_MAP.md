# Capability Map

Generated at: `2026-06-29T14:26:36Z`

This map is the living inventory of what the crop-search pipeline can do, what is only partially working, and what is not implemented yet. Future sessions should update it with `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map` whenever capabilities change.

## Status Legend

- `operational`: implemented and exercised against current artifacts or tests.
- `configured`: implemented enough to run, but not yet exercised end to end with fresh live data.
- `partial`: works for a constrained subset or has known quality gaps.
- `missing`: not implemented or not production-usable.

## Summary

| Area | Status | Can Do Now | Still Missing | Evidence |
| --- | --- | --- | --- | --- |
| Context continuity | `operational` | Maintains `AGENTS.md`, generated `docs/HANDOFF.md`, and this capability map. | No git history is available in this workspace, so docs remain the main memory mechanism. | `AGENTS.md`, `docs/HANDOFF.md`, `docs/CAPABILITY_MAP.md` |
| Parameter manifest | `operational` | Defines 126 reusable crop physiology, phenology, canopy, root, water, soil, nutrient, stress, establishment, quality, harvest, and management parameters and maps them into crop-specific query plans. | Extraction, normalization, and promotion rules do not yet semantically cover every expanded trait family. | `config/parameters/core-crop-parameters.json` v0.4.1; 19 families |
| Crop profiles | `operational` | Supports corn, soybean, wheat, rice, cotton, sunflower, and tomato profiles. | More crops require new profile JSON files and source-bias terms. | `config/crops/` |
| Crop relationship matrix | `configured` | Generates a dense `crop_id x crop_id` relationship matrix skeleton, source-tier-aware relationship query plans, and opt-in relationship discovery ledgers; the current 7-crop universe produces 49 ordered cells and preserves symmetric evidence with canonical relationship keys. Matrix cells are populated from validated relationship claims. | Live relationship fetch execution, in-session Opus extraction, review, and human acceptance remain manual gates. | `src/crop_search_framework/relationships.py`, `config/relationships/relationship-vocabulary.json`, `schemas/crop-relationship-*.schema.json` |
| Hybrid relationship evidence graph | `configured` | Layers a request-time evidence graph over the dense matrix for minor crops and aggregate nodes: `--pair-mode auto` plans unordered for symmetric modes / ordered otherwise (override with ordered|unordered); `--node-mode aggregate` plans group-level (family/functional-group/host-group) searches from the node catalog, steered to textbook/institution/extension tiers, so aggregate evidence can actually be discovered and extracted (not just consumed); symmetric modes (intercrop/strip_crop/mixed_crop/companion_crop) are canonicalized on load so one claim mirrors both ordered cells and resolves either ordering, while directional modes (rotation/relay_crop/…) stay one-directional; `build-relationship-graph` indexes evidence-bearing claims by (mode, subject_node, object_node); `resolve-crop-relationship` answers a pair from exact crop evidence, then cross-group inference (family > functional_group > genus, in direction), with host-risk caveat overlays. A routing guard blocks the same span from both the relationship and management-parameter lanes. | Directional-evidence assignment from neutral unordered sources is exercised by counts only until the Opus extraction lane lands; the resolver answers one mode per call (no per-mode aggregation yet); genus-level aggregate queries and quantitative LER synthesis are out of scope. | `src/crop_search_framework/relationships.py`, `src/crop_search_framework/relationship_pipeline.py`, `config/relationships/node-catalog.json`, `config/relationships/relationship-vocabulary.json` |
| Global tier-aware query planning | `operational` | Plans global searches across peer-reviewed science, textbook/reference, international institutions, extension/public agronomy, and industry/grower guides; `pilot-global-wheat-001` has been executed end to end. | Only the wheat global benchmark has been executed so far; rice, sunflower, and tomato global runs remain pending. | 2345 planned global queries across 5 run configs; 3 global run executed |
| Live web search | `partial` | Can call the local `search-web` tool, use source-tier-aware discovery connectors, and rank sources by crop, parameter, topic, and source-tier signals. | DuckDuckGo HTML search can still return empty results or 403s; Google Books rate limiting and peer-reviewed connector precision need provider-specific tuning. | `pilot-global-wheat-001`: 425 queries, 825 captured sources, 0 search failures, 300 source failures |
| Fetch and parse | `partial` | Fetches HTML/PDF and extracts raw text, snippets, publication hints, candidate claims, and lightweight evidence-fragment labels. | PDF/table parsing remains heuristic; CSV/table-heavy documents and scientific full-text structures need stronger semantic parsers. | `src/crop_search_framework/dev_tools/fetch_web.py`, `src/crop_search_framework/dev_tools/parse_document.py` |
| Claim cleanup | `partial` | Filters many source headers, bylines, navigation fragments, table captions, and layout artifacts. | Still needs semantic table extraction and better distinction between true recommendations and low-value descriptive text. | `src/crop_search_framework/quality.py`; 579 normalized U.S. pilot claims after cleanup |
| Claim normalization | `partial` | Normalizes temperature, GDU, water, date-window, text, attribute subtype, provenance, confidence, and conflict status for current pilots. | Unit coverage, crop-stage modeling, cultivar specificity, management recommendations, and non-temperature parameters need expansion. | `src/crop_search_framework/normalize.py` |
| Geolocation | `partial` | Separates claim applicability from source origin; geocodes U.S. states/counties with Census records, custom regions, and verified farm points. | Non-U.S. administrative geocoding and authoritative production-region polygons are not implemented yet. | `src/crop_search_framework/geocoding.py`, `data/gazetteer/` |
| Review and durable promotion | `operational` | Reviews normalized claims, flags conflicts, promotes canonical/regional/merge candidates, and writes durable claim artifacts. | Manual adjudication semantics are still basic; the expanded wheat run promoted only a small subset of normalized claims, so promotion needs stricter trait specificity and source-tier precedence. | `src/crop_search_framework/review.py`, `src/crop_search_framework/promote.py`; 102 promoted U.S. pilot claims, 165 promoted global pilot claims |
| Parameter coverage reporting | `operational` | Reports requested, normalized, promoted, missing, needs-review, and source-tier-specific parameter coverage. | Coverage scoring reports tier heterogeneity but does not yet adjudicate scientific-vs-regional precedence. | `src/crop_search_framework/coverage.py`, `exploration/coverage/` |
| PostgreSQL path | `partial` | Exports load-ready SQL and can load if `POSTGRES_DSN` is configured. | No persistent staging database workflow, migrations runner, or deployment environment is established. | `src/crop_search_framework/postgres_loader.py`, `data/postgres/` |
| Evaluation and CI | `partial` | Has unit tests for normalization/review, source tiers, geocoding, parser cleanup, and handoff rendering. | No formal CI workflow, golden extraction set, precision/recall dashboard, or live-run regression suite yet. | `tests/`; current test count tracked by unittest output |
| Scientific and textbook evidence handling | `partial` | Plans searches for peer-reviewed and textbook/reference tiers, records source-tier metadata, captures metadata-only sources, and discovers from OpenAlex, Crossref, Google Books, and Open Library. | The wheat benchmark produced textbook/reference normalized claims but zero peer-reviewed normalized claims; Semantic Scholar/PubMed connectors, stronger scholarly queries, Google Books retry/backoff, and paywalled text extraction remain missing. | `pilot-global-wheat-001`: 819 textbook/reference normalized claims, 159 peer-reviewed normalized claims |

## Current Artifact Totals

| Metric | Value |
| --- | ---: |
| Core parameter catalog parameters | 126 |
| Core parameter catalog families | 19 |
| U.S. pilot raw candidate claims | 0 |
| U.S. pilot normalized claims | 579 |
| U.S. pilot promoted durable claims | 102 |
| U.S. pilot manual-review claims | 335 |
| Global pilot runs executed | 3 |
| Global pilot raw candidate claims | 4386 |
| Global pilot normalized claims | 4650 |
| Global pilot promoted durable claims | 165 |
| Global pilot manual-review claims | 2055 |
| Global pilot peer-reviewed normalized claims | 159 |
| Global pilot textbook/reference normalized claims | 819 |
| Global tier-aware run configs | 5 |
| Planned global tier-aware queries | 2345 |

## Global Query Plans

| Run config | Planned queries |
| --- | ---: |
| `config/runs/pilot-global-rice.json` | 490 |
| `config/runs/pilot-global-sunflower.json` | 455 |
| `config/runs/pilot-global-tomato.json` | 420 |
| `config/runs/pilot-global-wheat-002.json` | 490 |
| `config/runs/pilot-global-wheat.json` | 490 |

## Update Rule

Update this map when any of these change:

- New pipeline stage, source tier, parser, normalizer, review rule, loader, or geocoder capability.
- A capability moves between `missing`, `partial`, `configured`, and `operational`.
- New live run results materially change known quality or coverage.
- New constraints are discovered.

Refresh command:

```bash
PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map
PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff
```
