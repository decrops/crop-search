# Project Handoff

Generated at: `2026-06-24T09:49:28Z`

This is the canonical start-here file for future sessions. Read this first, then use the referenced files only as needed.

## Current Goal

Build a provenance-aware crop search runner that discovers crop physiological parameters and management recommendations from legally accessible sources, normalizes them into structured claims, preserves source/claim geolocation, reviews them for promotion, and exports load-ready records.

## Current State

- The local pipeline can run search, fetch, parse, normalize, review, promote, coverage, and PostgreSQL SQL export.
- The current production direction is global and source-tier-aware, not U.S.-only.
- The core parameter manifest now contains 126 crop physiology, phenology, canopy, root, water, soil, nutrient, stress, establishment, quality, harvest, and management parameters across 19 families.
- Source tiers are implemented for peer-reviewed science, open textbook/reference material, international institutions, extension/public agronomy publications, and industry/grower guides.
- Peer-reviewed and textbook/reference discovery uses OpenAlex, Crossref, Google Books, and Open Library connectors before DuckDuckGo fallback, with metadata-only capture for inaccessible papers/books.
- U.S. state/county geocoding uses 2025 Census Gazetteer records; named production regions remain explicit custom approximate records.
- Existing U.S. pilot artifacts are retained as fixture-like evidence for parser, normalization, review, promotion, coverage, geolocation, and SQL export behavior.
- `pilot-global-wheat-001` has been run end to end: 425 live queries, 825 captured sources, 4386 candidate claims, 3622 normalized claims, 19 promoted claims, and SQL export generated.
- Existing `pilot-global-wheat-001` artifacts match the current comprehensive wheat config: 425 planned queries and 425 executed queries.
- The global wheat tier metrics moved beyond extension-heavy evidence by normalized-claim count: textbook/reference 819, international institutions 1091, industry/grower 538, extension 1015, peer-reviewed 159.
- Peer-reviewed discovery is still metadata-heavy: 255 peer-reviewed captures produced 159 normalized claims, so scholarly full-text retrieval and query precision remain the main evidence gap.

## Start Here

1. Read this file.
2. Read `docs/CAPABILITY_MAP.md` for the current can/cannot-do inventory.
3. Read `README.md` for command overview.
4. Read `docs/IMPLEMENTATION_LOG.md` only if you need historical detail.
5. Use `config/runs/pilot-global-wheat.json` as the next-run template unless the user asks for a different crop or geography.

## Next Recommended Step

Use `pilot-global-wheat-001` as the benchmark for the next implementation pass: improve extraction/normalization for the new trait families, tighten peer-reviewed discovery/query terms, add provider-specific book connector retry/backoff, improve PDF/book parsing, and make promotion reject broad non-trait descriptive claims.

After those fixes, rerun the wheat benchmark as a regression check:

```bash
PYTHONPATH=src python3 -m crop_search_framework.cli run-exploration --run-config config/runs/pilot-global-wheat.json
PYTHONPATH=src python3 -m crop_search_framework.cli normalize-run pilot-global-wheat-001
PYTHONPATH=src python3 -m crop_search_framework.cli review-run pilot-global-wheat-001
PYTHONPATH=src python3 -m crop_search_framework.cli promote-run pilot-global-wheat-001
PYTHONPATH=src python3 -m crop_search_framework.cli coverage-run pilot-global-wheat-001
PYTHONPATH=src python3 -m crop_search_framework.cli load-postgres pilot-global-wheat-001
PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map
PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff
```

## Global Run Configs

| Run config | Run ID | Query count | Notes |
| --- | --- | ---: | --- |
| `config/runs/pilot-global-rice.json` | `pilot-global-rice-001` | 425 | global scope, source-tier policy, no seed fallback |
| `config/runs/pilot-global-sunflower.json` | `pilot-global-sunflower-001` | 390 | global scope, source-tier policy, no seed fallback |
| `config/runs/pilot-global-tomato.json` | `pilot-global-tomato-001` | 355 | global scope, source-tier policy, no seed fallback |
| `config/runs/pilot-global-wheat-002.json` | `pilot-global-wheat-002` | 425 | global scope, source-tier policy |
| `config/runs/pilot-global-wheat.json` | `pilot-global-wheat-001` | 425 | global scope, source-tier policy, executed live |

## Existing Artifact Snapshot

| Run ID | Raw candidate claims | Normalized | Manual review | Rejected | Promoted | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `pilot-us-corn-iowa-001` | 0 | 33 | 11 | 0 | 12 | 9 normalized / 7 promoted / 3 missing |
| `pilot-us-wheat-001` | 0 | 124 | 102 | 7 | 15 | 3 normalized / 2 promoted / 9 missing |
| `pilot-us-rice-001` | 0 | 80 | 0 | 65 | 15 | 3 normalized / 3 promoted / 9 missing |
| `pilot-us-sunflower-001` | 0 | 160 | 67 | 0 | 33 | 5 normalized / 1 promoted / 7 missing |
| `pilot-us-tomato-001` | 0 | 182 | 155 | 0 | 27 | 4 normalized / 3 promoted / 8 missing |
| `pilot-global-wheat-001` | 4386 | 3622 | 1461 | 1292 | 19 | 12 normalized / 6 promoted / 73 missing |

## Key Files

- `config/source-tiers/default.json`: source-tier policy for comprehensive accessible evidence.
- `config/runs/pilot-global-*.json`: global, tier-aware run configs for upcoming searches.
- `src/crop_search_framework/parameters.py`: parameter and source-tier query planning.
- `src/crop_search_framework/source_tiers.py`: source-tier manifest loading and scoring signals.
- `src/crop_search_framework/dev_tools/discovery_connectors.py`: OpenAlex, Crossref, Google Books, and Open Library discovery adapters.
- `src/crop_search_framework/coverage.py`: parameter and source-tier coverage reporting.
- `src/crop_search_framework/geocoding.py`: Census-backed state/county geocoding plus custom production regions.
- `src/crop_search_framework/normalize.py`: claim normalization, geolocation inference, conflict grouping.
- `docs/CAPABILITY_MAP.md`: generated inventory of operational, configured, partial, and missing pipeline capabilities.
- `docs/IMPLEMENTATION_LOG.md`: chronological track record.
- `docs/ROADMAP.md`: remaining implementation phases.

## Known Constraints

- Live global execution has only been benchmarked for wheat; rice, sunflower, and tomato global configs are still pending.
- The comprehensive wheat run is much larger than the original 60-query pilot, so future live execution should use batching/rate-limit controls and progress logging.
- Google Books was rate-limited during the wheat benchmark, so textbook/reference normalized claims came from DuckDuckGo-discovered open sources rather than book APIs.
- The peer-reviewed tier captured mostly Crossref metadata-only records and produced zero normalized trait claims in the wheat benchmark.
- The expanded run queried 85 parameters, but current normalization/review rules only produced normalized claims for a small subset of requested parameters.
- Paywalled scientific papers and books may be used for metadata discovery only unless accessible full text is available.
- County extraction is implemented and test-covered, but current pilot artifacts do not yet contain state-qualified county claims after cleanup.
- SQL export exists and local/disposable PostgreSQL has been tested earlier, but a persistent staging database workflow is still not established.
- This workspace git status is `not initialized in this workspace`; docs and generated artifacts are the continuity source unless a repo is initialized.

## Validation Baseline

Use these commands after implementation work:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map
PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff
```

Last implementation-log section detected: `2026-06-24: Planning — crop relationship matrix`.

## Automatic Session Rule

Future sessions should read `AGENTS.md`, this file, and `docs/CAPABILITY_MAP.md` before making changes. Before ending a session that changes code, configs, generated artifacts, docs, or pipeline capabilities, run `PYTHONPATH=src python3 -m crop_search_framework.cli write-capability-map` and `PYTHONPATH=src python3 -m crop_search_framework.cli write-handoff`; update `docs/IMPLEMENTATION_LOG.md` for meaningful milestones.
