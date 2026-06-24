# Pipeline Extension: Relationship Lane Completion + Crop-Protection Gap Closure

**Status:** Plan (awaiting build approval)
**Date:** 2026-06-24
**Builds on:** [crop-relationship-matrix-plan.md](crop-relationship-matrix-plan.md) (relationship subsystem through discovery) and [pipeline-quality-rework-plan.md](pipeline-quality-rework-plan.md) (the reworked single-crop acquisition/extraction pipeline).

Two independent goals, treated as **two lanes that never cross**:

1. **Relationship lane** — finish the crop×crop pipeline past discovery (select → fetch → extract → populate matrix). Schema + discovery already exist; the back half does not.
2. **Crop-protection gap** — the disease/pest/weed parameters exist in the manifest but are `stub`, so they were never searched or extracted. Close the gap by activating them and running the *existing* parameter pipeline against region-relevant sources.

A third, smaller item covers the **inherently-local gaps** (lime/fertilizer rates, exact sowing dates) that no corpus can fully close.

---

## 0. Current state (verified)

**Relationship lane — built:**
- `config/relationships/relationship-vocabulary.json` — 9 modes (rotation, continuous_cropping, double_crop, intercrop, relay_crop, strip_crop, mixed_crop, companion_crop, cover_crop).
- [relationships.py](../src/crop_search_framework/relationships.py) — matrix builder, pair-aware query planner, `discover_relationships` → `exploration/relationships/discovery/<run_id>/results.jsonl`. Rows carry `subject_crop_id`, `object_crop_id`, `relationship_mode`, `relationship_subtype`, `directionality`, `ordered_pair_key`, `canonical_relationship_key`, `relationship_source_key`, score/components, provider, access_status. Dedup keys on `relationship_source_key` ([relationships.py:560](../src/crop_search_framework/relationships.py#L560)), not URL.
- CLIs: `write-relationship-matrix`, `plan-relationship-queries`, `discover-relationships`.
- Schemas: `crop-relationship-matrix.schema.json` (cells have `mode_statuses` keyed by mode), `crop-relationship-query-plan.schema.json`, `crop-relationship-claim.schema.json` (the extraction target — subject/object crop+group, mode, subtype, direction, effect, value, context, mechanisms, provenance, confidence, status), `crop-relationship-vocabulary.schema.json`.
- [raw-capture.schema.json](../schemas/raw-capture.schema.json) extended with optional `query_kind`, `subject_crop_id`, `object_crop_id`, `relationship_mode`, `relationship_subtype`, `ordered_pair_key`, `canonical_relationship_key`.

**Relationship lane — missing:** `select-relationship-fetch`, `fetch-relationships`, `extract-relationships`, `populate-relationship-matrix`.

**Crop-protection — the gap is 15 authored-but-inactive `stub` parameters** (verified):

| family | parameter_ids | per-param `query_terms`? |
|---|---|---|
| `crop_protection` (12) | `key_disease_pressure`, `key_pest_threshold`, `weed_management`, `disease_sensitive_stage`, `fungicide_application_timing`, `insect_action_threshold`, `pest_sensitive_stage`, `critical_weed_free_period`, `herbicide_application_window`, `seed_treatment_recommendation`, `nematode_pressure`, `disease_severity_threshold` | first 3 **NO**, other 9 yes |
| `variety` (2) | `variety_cultivar.disease_resistance_package`, `variety_cultivar.herbicide_tolerance_trait` | first **NO**, second yes |
| `post_harvest` (1) | `post_harvest_quality.storage_pest_monitoring` | yes |

**All `implementation_status: stub`** → excluded by every active-only filter ([parameters.py:67](../src/crop_search_framework/parameters.py#L67), [llm_extract.py:85](../src/crop_search_framework/llm_extract.py#L85), etc.). Two correctness facts the rest of the plan depends on:

1. **`family` ≠ `domain`.** Selection by `parameter_families` filters the manifest `family` field ([parameters.py:63](../src/crop_search_framework/parameters.py#L63), [:71](../src/crop_search_framework/parameters.py#L71)). The protection set spans **three families** (`crop_protection`, `variety`, `post_harvest`) even though it spans only two domains. So a family/domain filter cannot select this set cleanly — **Phase 1 must use an explicit `parameter_ids` list.**
2. **`query_terms` is not universal.** 11 of the 15 carry per-param `query_terms`; **4 do not** — `crop_protection.key_disease_pressure`, `.key_pest_threshold`, `.weed_management`, `variety_cultivar.disease_resistance_package`. Those 4 fall back to their domain's query template; `crop_protection`'s template block is IPM-ish, but `variety_cultivar`'s is variety-trial idiom (no disease vocab). So Phase 1 must **add per-param `query_terms` to those 4** (see B1).

`config/query-templates/default.json` already has a `crop_protection` domain block (action threshold, scouting, growth stage, management guide).

---

## 1. Lane A — Relationship pipeline completion

Mirrors the single-crop lane (discover → select-fetch → fetch → extract → aggregate) but is **parameter-free**: nothing here touches `EXTRACTION_KEYS` or `parameter_id`.

### A1 — `select-relationship-fetch <run_id>`
- New `relationship_fetch_selection.py`, modeled on [fetch_selection.py](../src/crop_search_framework/fetch_selection.py).
- Consumes `exploration/relationships/discovery/<run_id>/results.jsonl`; emits `.../fetch_queue.jsonl`.
- **Dedup unit = `relationship_source_key`**, NOT `canonical_key`/URL — one document legitimately supports many pairs, so a source appears once per pair it evidences. Aggregate the contributing discovery rows per `relationship_source_key`, preserving `ordered_pair_key[]` / `canonical_relationship_key[]` and `subject/object_crop_id`.
- Reuse the tier-aware domain caps + trusted-domain allowlist + article-like/junk filters from the parameter policy (new `config/fetch-policy/relationships.json` or reuse default). Balance per **mode × pair** coverage instead of per parameter (target N sources per pair-mode).
- Verify: a source supporting 3 pairs yields 3 selectable pair-rows that share one underlying fetch; no pair is dropped purely for URL-duplication.

### A2 — `fetch-relationships --run-config … <run_id>`
- New `relationship_fetch_stage.py`, modeled on [fetch_stage.py](../src/crop_search_framework/fetch_stage.py): fetch selected URLs via the shared binary `HttpClient` (cache/backoff/resume), parse via `parse_document`, write **relationship raw captures** to `exploration/relationships/raw/<run_id>/` that carry the optional relationship fields (`query_kind="crop_relationship"`, `subject_crop_id`, `object_crop_id`, `relationship_mode`, `relationship_subtype`, `ordered_pair_key`, `canonical_relationship_key`) — the raw-capture schema already accepts them.
- **One capture per (document, canonical_relationship_key)** so a multi-pair document produces one capture per pair it evidences (parallels the doc×param captures in the parameter lane). Fetch once per URL (HTTP cache dedups), emit per pair.
- **Carry the replay-key lesson from the parameter run:** write captures keyed by `document_id` from the start (not `<run>-capture-NNN`), so the corpus build (A2.5) and the extractor (A3) resolve text + blocks by `document_id`.

### A2.5 — Build the relationship corpus (the join A3 needs)
- A2 writes raw captures only; A3 needs `document_id` + blobs + block tables. The existing [corpus.py](../src/crop_search_framework/corpus.py) builder produces exactly that (`documents/`, `documents/blobs/`, `blocks/`, `query_hits.jsonl`, [corpus.py:256](../src/crop_search_framework/corpus.py#L256), [:268](../src/crop_search_framework/corpus.py#L268)). Add a relationship corpus build — either reuse `build-corpus` pointed at `exploration/relationships/raw/<run_id>/`, or a thin `build-relationship-corpus` wrapper that additionally carries the pair fields into a `relationship_hits.jsonl` (the analog of `query_hits.jsonl`, associating `document_id → canonical_relationship_key/pair/mode`). Output under `exploration/relationships/corpus/<run_id>/`.
- This gives A3 a stable `document_id`-keyed blob+block store and tells it which pairs each document is supposed to evidence.

### A3 — `extract-relationships <run_id>` (separate extractor, separate schema)
- New `relationship_extract.py`. **Does not** use the parameter extractor, `EXTRACTION_KEYS`, or the `parameter_id` enum. Emits objects validated against `crop-relationship-claim.schema.json`.
- Engine = subagent fan-out (same pattern used for the wheat-002 Opus pass): each agent reads a relationship-corpus document's **blob text + block tables** (from A2.5) and the **pair context** (`relationship_hits.jsonl`: which `canonical_relationship_key`/`subject`/`object`/`mode` this document should evidence), emits claims with: `relationship_claim_id`, `run_id`, `subject_crop_id`/`object_crop_id` (+ `_crop_group` from crop profiles), `relationship_mode`, `relationship_subtype`, `direction` (enum: object_precedes_subject | subject_precedes_object | simultaneous | bidirectional | same_crop | not_directional | unknown), `ordered_pair_key`, `canonical_relationship_key`, `effect` (beneficial | compatible | conditional | neutral | incompatible | avoid | unknown), `claim_text`, `evidence_text`, `value`, `context` (arrangement, row_ratio, temporal_offset, disease_pressure, water_regime, …), `mechanisms[]`, `provenance` (incl. block anchors), `confidence`, `status`.
- Output: `exploration/relationships/claims/<run_id>/<document_id>.json` = `{"claims":[…]}`. Validate each against the schema.
- **Routing rule (the one cross-lane contract):** a *named, directional pair* statement ("corn after soybean improves yield"; "wheat–faba bean intercrop") → relationship claim. *Broad, non-pair advice* ("rotate wheat with a non-cereal", "avoid cereal-on-cereal") → the single-crop `management.rotation_recommendation` claim in the parameter lane. **The same evidence span is never emitted to both.** Encode this as an explicit instruction in the extractor prompt and as a dedup check keyed on `(evidence_text hash)` across both lanes' outputs.

### A4 — `populate-relationship-matrix <run_id>`
- New `relationship_matrix.py`: matrix skeleton (`write-relationship-matrix`) + relationship claims + discovery ledger → populated `crop-relationship-matrix.schema.json` with per-cell `mode_statuses`:
  - `not_searched` — no discovery rows for that pair×mode.
  - `searched_no_evidence` — discovery rows exist, no accepted claim.
  - `evidence_found` — ≥1 accepted claim.
  - `conflicting_evidence` — accepted claims disagree on `effect` (e.g. beneficial vs avoid) for the same pair×mode×context.
- **Symmetric modes** (intercrop, mixed, companion, strip, cover where order is immaterial): store the evidence once under `canonical_relationship_key`, then **mirror the summary into both ordered cells**. **Directional modes** (rotation, double_crop, relay, continuous): keep per `ordered_pair_key`.
- Emit a coverage rollup: cells searched / with evidence / conflicting, per mode.

### A5 — relationship eval (small gold)
- `tests/golden/relationships/<mode>.json`: a few known pairs with expected `effect`/`direction` (e.g. wheat-after-soybean = beneficial; continuous wheat = incompatible/avoid). `eval-relationships` scores effect-accuracy + direction-accuracy + pair recall against the matrix, mirroring `eval_harness`.

---

## 2. Lane B — Crop-protection gap closure (reuses the existing parameter pipeline)

The gap is **not** missing infrastructure — it's 14 inactive parameters + a corpus that never targeted IPM. No new extractor needed.

### B1 — Activate the parameters (data-only) — **scope decision: 14, not 15**
- **Phase 1 set = the 14 field-protection params** (12 `crop_protection.*` + 2 `variety_cultivar.*`). **Defer `post_harvest_quality.storage_pest_monitoring`** — it's a storage/post-harvest concern, not the field disease/pest/weed gap from the Freiburg grow plan; activate it later with a post-harvest phase. This makes the arithmetic consistent: **active 85 → 99.**
- Flip those 14 to `implementation_status: active` in `config/parameters/core-crop-parameters.json` (bump to 0.4.1).
- **Add per-param `query_terms` to the 4 that lack them** (IPM idiom), so they don't fall back to non-disease domain templates: `key_disease_pressure` → `["disease pressure","epidemic risk","inoculum","regional disease survey"]`; `key_pest_threshold` → `["pest threshold","economic threshold","action threshold","scouting"]`; `weed_management` → `["weed control","herbicide program","integrated weed management","resistance"]`; `variety_cultivar.disease_resistance_package` → `["disease resistance rating","resistance package","Septoria rating","rust resistance"]`.
- Confirm each of the 14 has a sensible `value_type`, `normalized_attribute_subtype`, and `review_policy` (verified present).
- Consequence checks (tests are version-agnostic): query-plan counts rise; extraction enum 85 → 99; coverage matrix expands. Run the suite to confirm no regressions.

### B2 — Region-aware queries + IPM seed sources
- The crop-protection physiology values are **region-specific** (Septoria/Fusarium/rust pressure, fungicide timing, herbicide windows differ by climate). Add a **region-scoped run config** `config/runs/eu-wheat-protection.json`, `region_scope` = Germany/Upper-Rhine/Europe, selecting the 14 params by **explicit `parameter_ids`** (NOT `parameter_families` — the set spans families `crop_protection`/`variety`, so a family filter would silently omit the two variety params). The params are `requires_stage_context`, so queries carry BBCH/growth-stage vocab automatically.
- Extend `config/query-templates/default.json` `crop_protection` block with disease/agent idiom: `["Septoria","Fusarium head blight","yellow rust","brown rust","fungicide T1 T2 T3","BBCH","action threshold","economic threshold","pre-emergence herbicide","resistance group"]`.
- Add curated IPM seeds to `config/seeds/wheat.json` (and a new EU subset): AHDB wheat disease-management / fungicide-performance guides, Julius Kühn-Institut / regional Pflanzenschutz advisories, university extension fungicide-timing + weed-control guides, EuroWheat/RustWatch. Tag with `covered_parameters` (fungicide_application_timing, disease_sensitive_stage, critical_weed_free_period, herbicide_application_window, …). `seeds-validate` confirms the param ids now resolve (they will, post-activation).

### B3 — Run the existing pipeline against protection
- `discover → select-fetch → fetch → build-corpus → backfill → corpus-qa → (Opus extract) → normalize → review → promote → render-vault`, on the region-scoped protection run. Discovery injects the IPM seeds (the seed-injection fix from the wheat-002 run) and the queries carry disease idiom, so the corpus will contain extension/peer-reviewed protection content.
- Extraction reuses the **single-crop** contract (these are `parameter_id`-mapped facts, not pair facts) → flows into the same normalize/review/promote and the vault as new data-point notes.
- **Cache-key requirement (carry the wheat-002 lesson):** `eval-extraction` loads `exploration/llm_cache/<run>/<document_id>.json` ([eval_harness.py:163](../src/crop_search_framework/eval_harness.py#L163)), but the LLM/Fixture backends replay by `capture["id"]` ([llm_extract.py:475](../src/crop_search_framework/llm_extract.py#L475)). These only line up when captures are **document-keyed** (`capture.id == document_id`). So the protection extraction MUST emit document-keyed cache files (one per `document_id`) and the normalize step must run over **document-keyed captures** — exactly the regenerate-one-capture-per-doc step used in wheat-002. Bake this into the run so `eval-extraction` and `normalize --from-llm` both resolve. (Better: fix `fetch_stage` to optionally write doc-keyed captures so this is automatic — tracked as a small follow-up.)

### B4 — Protection eval gold
- Add `tests/golden/extraction/crop_protection.json` with a few verifiable facts (e.g. Septoria T2 fungicide at flag-leaf/BBCH 37–39; critical weed-free period), each keyed by the real `document_id` it should be extracted from. This only guards the gate if B3's cache is document-keyed (above); otherwise `eval-extraction` finds nothing to adjudicate. Verify the gold doc_ids exist in `exploration/llm_cache/<run>/` before relying on the gate.

---

## 3. Lane C — Inherently-local gaps (lime/fertilizer rates, exact sowing dates)

These cannot be fully closed by any global corpus — they depend on a soil test and the running season. Mitigations rather than extraction:
- **Region-scoped extension seeds** (Lane B's EU run) capture *local rate frameworks and date windows* (e.g. regional N-recommendation formulas, BBCH-anchored sowing windows) even if not a single number — better than the global vault's wide ranges.
- **Vault "local calibration" stub:** render a per-crop `Wheat — Local calibration (Freiburg).md` note that explicitly lists what must come from a soil test (lime to target pH, P/K/N base rates) and from the season (exact sowing date within the vault window), linking the relevant data-point notes. Marks the gap as *known and bounded* instead of silently absent.
- **Unit-normalization cleanup** (carried over from the prior run): normalize the unit-mixed parameters (seeding_rate, rooting_depth, plant_density, water demand, °F/°C) so the local-calibration note cites clean numbers.

---

## 4. Phasing

- **Phase 1 — Crop-protection gap (fastest value, reuses everything):** B1 activate stubs → B2 templates+seeds → B4 gold → B3 run pipeline (region-scoped). Closes the Freiburg disease/pest/weed gap with no new code beyond config + a run config.
- **Phase 2 — Relationship back-half (code):** A1 select-relationship-fetch → A2 fetch-relationships → **A2.5 build relationship corpus** → A3 extract-relationships → A4 populate-relationship-matrix → A5 eval. Each with unit tests mirroring the parameter-lane tests.
- **Phase 3 — Lane C:** unit-normalization pass + local-calibration vault note; re-render.
- **Phase 4 — Run relationships live:** `write-relationship-matrix → plan-relationship-queries → discover-relationships → select-relationship-fetch → fetch-relationships → extract-relationships → populate-relationship-matrix` for rotation first (49 pairs), then intercrop.

---

## 5. Acceptance gates

**Relationship lane:**
- `select-relationship-fetch` dedups by `relationship_source_key`; a 3-pair source survives as 3 pair-rows. Verified by test.
- relationship raw captures carry all pair fields and validate against `raw-capture.schema.json`.
- A2.5 produces a `document_id`-keyed blob+block store + `relationship_hits.jsonl`; A3 reads blocks (not raw captures) and never has to guess pair context.
- every emitted claim validates against `crop-relationship-claim.schema.json`; 0 use `parameter_id`.
- **no evidence span appears in both** a relationship claim and a `management.rotation_recommendation` claim (routing test).
- populated matrix: every searched pair×mode has a status; symmetric modes mirror into both ordered cells; conflicting effects flagged `conflicting_evidence`.
- `eval-relationships`: effect-accuracy + direction-accuracy reported on the gold pairs.

**Crop-protection:**
- active param count **85 → 99** (14 activated; `storage_pest_monitoring` deferred); the 4 query_terms-less params now carry IPM terms; run config selects by explicit `parameter_ids` and the query plan actually contains all 14 (incl. the 2 `variety` params). Suite green.
- protection extraction writes **document-keyed** cache files, so `eval-extraction` and `normalize --from-llm` both resolve.
- corpus-qa for the protection run passes the standard gates; ≥1 accepted claim for each of fungicide_application_timing, disease_sensitive_stage, critical_weed_free_period (eval gold, doc-id-keyed).
- new data-point notes render for the protection families.

---

## 6. Explicit non-goals / guardrails
- Relationship claims never enter `core-crop-parameters.json` or `parameter_id` space.
- Single-crop and relationship lanes share infrastructure (HttpClient, parse_document, corpus block store, subagent extraction pattern) but have **separate ledgers, captures, claims, and outputs**.
- Routing is one-way and exclusive (named pair → relationship; broad → management), enforced by prompt + cross-lane span-dedup.
- Lane C numbers that depend on soil test / season are surfaced as a calibration checklist, not fabricated.

---

## 12. Implementation status (2026-06-24)

Built + tested offline (137 tests OK, +13; compileall clean). Live crawls, Opus extraction passes, and vault writes are **gated** (not executed).

**Phase 1 — crop-protection (DONE, code/data):**
- Activated 14 stubs → `active` (85→**99**), manifest **0.4.1**; `storage_pest_monitoring` deferred. Added IPM `query_terms` to the 4 that lacked them.
- `config/runs/eu-wheat-protection.json` (region Germany, **explicit `parameter_ids`**); query plan yields 13 wheat-applicable params (nematode_pressure correctly excluded by crop-group), incl. both `variety` params.
- Extended `crop_protection` query template (Septoria/Fusarium/rust/T1-T3/BBCH/herbicide); +7 curated IPM seeds (`seeds-validate` clean, 17 seeds).
- `tests/golden/extraction/crop_protection.json` (pending; facts in `_pending_records` until real doc_ids exist). Tests updated for the new active count (dynamic) + still-stub example.

**Phase 2 — relationship back-half (DONE, code):** `relationship_pipeline.py` + 6 CLIs — `select-relationship-fetch` (dedup by `relationship_source_key`), `fetch-relationships` (pair-tagged doc-keyed captures), `build-relationship-corpus` (reuses generalized `build_corpus` + emits `relationship_hits.jsonl`), `validate-relationship-claims` (→ `crop-relationship-claim.schema.json`), `populate-relationship-matrix` (per-cell `mode_statuses`: not_searched/searched_no_evidence/evidence_found/conflicting_evidence; symmetric modes mirror via shared canonical key), `eval-relationships` (+ `tests/golden/relationships/rotation.json`). `build_corpus` generalized with `raw_dir`/`out_dir` overrides. 6 tests incl. fetch→corpus join.

**Phase 3 — units + calibration (DONE, code):** `unit_normalize.py` (`normalize-units`: °F→°C safe convert, flag-only for unit-mixed params; on wheat-002: 7 converted, 10 flags across 5 params) + `calibration.py` (`render-calibration`: per-crop local-calibration note, marker-guarded, dry-run default). 7 tests.

**Gated next steps (need network / Opus / vault write):**
- Phase 1 B3: live `discover → select-fetch → fetch → build-corpus → backfill → corpus-qa` on `eu-wheat-protection-001`, then Opus extraction (doc-keyed cache) → normalize/review/promote → fill the pending gold doc_ids → render-vault.
- Phase 4: live relationship crawl (`discover-relationships → select-relationship-fetch → fetch-relationships → build-relationship-corpus → extract (Opus subagents) → populate-relationship-matrix → eval-relationships`), rotation first.
- `render-calibration … ` write to the real vault.
