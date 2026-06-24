# Domain × Status Coverage Matrix

Generated from `config/parameters/core-crop-parameters.json` (manifest_version 0.4.0). Active = searched + scored; stub = visible placeholder, excluded from query planning and yield; deferred = known-but-later.

| Domain | active | stub | deferred | trait | operation | stage-dependent |
|---|---:|---:|---:|---:|---:|---:|
| climate_site | 7 | 0 | 0 | 6 | 1 | 0 |
| crop_protection | 0 | 12 | 0 | 1 | 11 | 9 |
| economics | 0 | 1 | 7 | 3 | 5 | 0 |
| growth_monitoring | 30 | 0 | 0 | 30 | 0 | 0 |
| harvest | 4 | 0 | 0 | 4 | 0 | 0 |
| nutrient_management | 7 | 0 | 0 | 2 | 5 | 1 |
| planting_establishment | 6 | 1 | 0 | 1 | 6 | 0 |
| post_harvest_quality | 4 | 7 | 0 | 5 | 6 | 0 |
| soil_prep_tillage | 3 | 4 | 0 | 0 | 7 | 1 |
| soil_requirements | 7 | 0 | 0 | 3 | 4 | 0 |
| stress_abiotic | 10 | 0 | 0 | 10 | 0 | 4 |
| variety_cultivar | 0 | 8 | 0 | 8 | 0 | 0 |
| water_management | 7 | 1 | 0 | 5 | 3 | 2 |
| **total** | **85** | **34** | **7** | **78** | **48** | **17** |

## Concept Scope (Active Parameters Only)

| concept_scope | count |
|---|---:|
| universal | 61 |
| crop_group | 24 |

## Non-Active Backlog

- **variety_cultivar** — 8 stub(s), 0 active. Candidate cultivar/variety traits are visible but still need crop/cultivar-scoped extraction and review semantics before activation.
- **crop_protection** — 12 stub(s), 0 active. Candidate IPM concepts are visible; activation should wait for organism tagging and stage-aware extraction.
- **economics** — 1 stub(s), 7 deferred. Economic concepts remain blocked until normalized values can carry currency, price year, area unit, region, and production system cleanly.
- **post_harvest_quality** — 7 stub(s), 4 active. Storage and drying concepts are promising first activation candidates because they are often unit-bearing and table-friendly.
