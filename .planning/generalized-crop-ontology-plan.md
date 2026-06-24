# Generalized, Farmer-Complete Crop Ontology — Plan (v2)

- **Status:** Draft for approval. Paper/config design only — no code or API calls in Phase 1.
- **Date:** 2026-06-22
- **Supersedes:** the v1 "generalized ontology" outline discussed in chat.
- **Related:** the LLM-extraction direction (replace the keyword extractor with Claude `messages.parse` over a manifest-as-enum). This plan folds that in and makes the extraction/review contract part of the ontology work, not a later wiring step.

---

## Verdict carried forward

The direction is approved in principle: decision-oriented ontology, explicit applicability tiers, and a BBCH phenology spine all fit the repo's manifest/profile architecture. v1 was **not** approvable as written because it treated extraction, claim schema, review, promotion, and query planning as downstream wiring. They are part of the ontology. v2 reorders the work around that, and shrinks Phase 1 from "150–200 parameters" to "restructure the existing 85 + a few obvious stubs."

The governing principle for v2: **an ontology field is only real once it survives into a normalized claim and changes a review/promotion decision.** Nicer manifest metadata that never reaches the claim is not progress.

---

## What changed from v1 (mapped to review findings)

1. **Expansion before extraction catches up (biggest risk).** The 85-parameter wheat run produced 2,338 normalized claims but only **11 parameters with claims, 1 promoted, 74 missing** ([docs/IMPLEMENTATION_LOG.md](../docs/IMPLEMENTATION_LOG.md) — "Expanded Global Wheat Rerun"). Adding 150–200 parameters now multiplies that gap.
   → **v2: Phase 1 restructures the existing 85 + a small set of obvious stubs. No jump to 200.** Breadth comes only after extraction yield is demonstrated on the restructured set.

2. **"No change to geocoding/provenance/promotion" was wrong.** The normalized-claim schema requires `parameter_id, attribute, attribute_subtype, value, scopes, provenance, observation_type` and carries **no** `domain`, `parameter_kind`, BBCH stage, operation target, or specificity ([schemas/normalized-claim.schema.json](../schemas/normalized-claim.schema.json)).
   → **v2: the normalized extraction contract is a Phase-1 deliverable.** New fields are designed to flow manifest → claim → review → promotion from the start.

3. **The manifest is not trait-only.** `category` already enumerates `management_recommendation` (+ `physiological_parameter`, `environmental_response`, `phenology_parameter`, `quality_parameter`), and the catalog already has ~20 management recommendations ([schemas/parameter-manifest.schema.json](../schemas/parameter-manifest.schema.json)).
   → **v2 reframing: don't "add" management — make existing management first-class: stage-aware, decision-aware, and reachable by extraction.** `parameter_kind` sharpens `category`; it does not replace it.

4. **Universal *concept* ≠ universal *value*.** "Soil pH range" or "N timing" are universal concepts, but values are crop-, region-, cultivar-, system-, and source-specific.
   → **v2 splits the two axes and reuses existing claim scopes:** `concept_scope` on the **parameter** (universal / group / crop) vs value specificity on the **claim** carried by the existing `entity`/`location_scope`/`time_scope` plus a new `agronomic_scope { cultivar, management_system }` — there is no separate `claim_applicability` blob. The earlier "T0/T1/T2 crop_specificity" idea becomes `concept_scope`.

5. **BBCH must be optional and evidence-backed per claim.** Crop profiles currently hold flat `growth_stage_terms` ([schemas/crop-profile.schema.json](../schemas/crop-profile.schema.json)), not a structured map.
   → **v2: `bbch_stage_map` is a many-to-many map (local term ↔ BBCH range) with per-entry confidence and source notes; claims carry an optional, evidence-backed `bbch_applicability` object** (distinct from the parameter's boolean `requires_stage_context`). No claim is forced onto a BBCH code without evidence.

6. **`{crop} × {stage} × tier` query expansion could explode.** Planning currently loops parameter patterns × source tiers only ([src/crop_search_framework/parameters.py](../src/crop_search_framework/parameters.py)).
   → **v2: stage expansion only for `requires_stage_context` parameters, under an explicit per-run query budget** with logged truncation.

7. **IPM/crop protection needs its own richer shape.** A fungicide/insect-threshold recommendation needs target organism, pressure/threshold, susceptible stage, control method, resistance group, and jurisdiction/label constraints — forcing it into flat parameter fields gets brittle.
   → **v2: crop protection is a typed sub-shape, deferred out of Phase 1** (stubbed only), designed after the core extraction contract is proven.

---

## The model (v2)

Three axes, kept deliberately separate:

### Axis 1 — Decision-oriented entries (refine, don't replace `category`)
Add `parameter_kind` ∈ `trait | operation`. Traits are facts (base temperature, optimal pH, oil content). Operations are stage-anchored actions (apply N at jointing, harvest at 14% moisture). `parameter_kind` is a sharper cut across the existing `category` enum and pairs with a required `decision` string ("what farmer action this informs").

### Axis 2 — Concept scope (parameter) vs value specificity (claim), reusing existing claim fields
- **`concept_scope`** on the parameter: `universal | crop_group | crop`. Replaces v1's `crop_specificity`. Drives authoring and the search plan. The existing `applies_to_crop_groups` remains the crop-group mechanism.
- **Value specificity reuses the claim's existing scope fields as the single source of truth — no duplicating blob.** What the extracted *value* applies to is recorded as:
  - **crop** → existing `entity.name`
  - **region** → existing `location_scope`
  - **season** → existing `time_scope`
  - **cultivar + management_system** → the *only* genuinely new dimensions → a new `agronomic_scope: { cultivar, management_system }`.
  Conflict/review logic reads the **combined applicability key** = `entity` + `location_scope` + `time_scope` + `agronomic_scope`. There is deliberately **no** `claim_applicability` object re-stating crop/region/season (that would create two sources of truth and let conflict logic drift). Never assume a universal concept (`concept_scope`) implies a universal value.

### Axis 3 — BBCH phenology spine (optional, evidence-backed) — two distinct shapes, two distinct names
- `config/reference/bbch.json`: principal growth stages, shared across crops.
- Crop profile gains `bbch_stage_map`: `[{ local_term, bbch_min, bbch_max, confidence, source_note }]` (many-to-many).
- **Parameter** gains optional **`requires_stage_context`** — a *boolean* meaning "this concept varies by stage; expand it in search." Nothing more.
- **Claim** gains optional **`bbch_applicability`** — an *object* `{ bbch_min, bbch_max, confidence, evidence_text }`, evidence-backed, omitted when no evidence supports a stage.
- The two are deliberately given **different names and different shapes** so a boolean parameter flag can never be wired into a claim's range object or vice versa.

---

## Schema deltas

### Parameter manifest ([parameter-manifest.schema.json](../schemas/parameter-manifest.schema.json))
Add to each parameter: `domain` (the 12 decision domains), `parameter_kind` (`trait|operation`), `concept_scope` (`universal|crop_group|crop`), `decision` (string), optional `requires_stage_context` (boolean), and `implementation_status` (`active | stub | deferred`). Keep `category`, `applies_to_crop_groups`, `required_scope`, `review_policy` as-is. Bump `manifest_version`.

### Normalized claim ([normalized-claim.schema.json](../schemas/normalized-claim.schema.json)) — **the load-bearing change; additive + backward-compatible**
- **Do not denormalize derivable metadata.** `domain` and `parameter_kind` are pure functions of `parameter_id`. Storing them on every claim creates **stale durable records** when the manifest changes. Instead: the claim stores `parameter_id` plus a `provenance.manifest_version` snapshot; `domain`/`parameter_kind` are **materialized at review/export time** from that manifest version. Opt into denormalization only if we explicitly want frozen historical labels.
- **Add only the non-derivable, value-specific fields:** `agronomic_scope: { cultivar, management_system }` (crop/region/season already live in `entity`/`location_scope`/`time_scope`) and optional `bbch_applicability { bbch_min, bbch_max, confidence, evidence_text }`.
- All additions are **optional** (see Schema evolution below), carried through `promote.py` and the Postgres load, and participate in review via the combined applicability key.

### Review & promotion ([review.py](../src/crop_search_framework/review.py), [promote.py](../src/crop_search_framework/promote.py))
- Compute conflicts within the same **combined applicability key** (`entity` + `location_scope` + `time_scope` + `agronomic_scope`) — two N-rate values for different management systems are not a conflict.
- Promotion gates use `parameter_kind`, applicability-key completeness, and `bbch_applicability` evidence — and reject `descriptive`/low-confidence claims (the current promotion bottleneck).
- `domain`/`parameter_kind` are materialized here from `provenance.manifest_version`, not read off the claim.

### Crop profile ([crop-profile.schema.json](../schemas/crop-profile.schema.json))
Add `bbch_stage_map` (many-to-many, with confidence + source notes). Optional thin `crop_parameters` overlay for `concept_scope: crop` entries.

### Crop protection (Phase 3+, typed sub-shape — stubbed only in Phase 1)
`{ target_organism, pressure_or_threshold, susceptible_stage (BBCH), control_method, resistance_group, jurisdiction/label_constraint }`. Not forced into flat parameter fields.

---

## The 12 decision domains (breadth-first, but populated incrementally)
Variety/cultivar · climate & site · soil requirements · soil prep/tillage · planting/establishment · water management · nutrient management · crop protection (IPM) · growth monitoring · stress/abiotic risk · harvest · post-harvest & quality (+ optional economics).

Phase 1 assigns every existing parameter to a domain and adds **only obvious missing stubs** so the domain map is visible end-to-end. **Stubs carry `implementation_status: stub`** (vs `active` for the restructured 85, `deferred` for known-but-later). This makes "breadth-first" operational: stubs make the domain map visible **but are excluded from query planning and from Phase 2 yield metrics** — they are placeholders, not searched concepts, so they never inflate or deflate measured coverage. Deep population (especially IPM, economics, post-harvest) waits until the extraction contract is proven.

---

## Query planning — does search actually take the ontology into account? ([parameters.py](../src/crop_search_framework/parameters.py))

**Today, search is driven only by** `evidence_patterns`/`search_aliases` (with a `{crop}` substitution **only**), `family`, `applies_to_crop_groups`, and `value_type`. There is no `{stage}`, no BBCH, and no `domain`/`parameter_kind`/`concept_scope` awareness in query generation. So the ontology reaches search **only** if Phase 2 changes this file explicitly. Four touchpoints, called out so they don't slip:

1. **Exclude non-active parameters from selection.** `selected_parameters` must filter `implementation_status != active` so stubs/deferred entries never generate queries.
2. **Union the crop-specific overlay into selection.** `selected_parameters` only reads `manifest["parameters"]`. `concept_scope: crop` entries in the crop profile's `crop_parameters` overlay must be merged in, or they are searched never.
3. **Stage expansion.** Add a `{stage}` placeholder to stage-anchored `evidence_patterns` **and** loop the crop profile's `bbch_stage_map` in `generate_parameter_queries` — only for `requires_stage_context` parameters, gated by a per-run **query budget** with logged truncation (no silent caps). Extends the existing `max_parameters` / `queries_per_parameter` / `query_terms_per_source_tier` knobs.
4. **`concept_scope`'s role in selection is explicit.** Selection continues to filter on `applies_to_crop_groups`; `concept_scope` is authoring/extraction metadata unless/until it is deliberately wired into `selected_parameters`. It does **not** silently change search.

The broadened manifest becomes the single enum target for the Claude extractor.

### Sequencing trap: re-extraction ≠ re-search
The Phase 2 wheat proof point **re-extracts the raw captures already on disk — it does not re-run search.** Consequences:
- New search behavior (stage expansion, overlay params, new patterns) is **not exercised** by the Phase 2 gate.
- Any genuinely new parameter will read as **"missing"** because nothing was ever crawled for it — a *search/coverage* gap, not an *extraction* gap. Coverage numbers must distinguish the two.

**Therefore Phase 2 gets two distinct validations:**
- **2a — Re-extraction (existing captures):** proves the extractor + claim contract + dedup on already-crawled text. Coverage is reported **only over parameters that have ≥1 capture**, so extraction quality isn't blamed for un-crawled topics.
- **2b — Fresh-crawl spike (small, live):** a budgeted live run for wheat over the restructured manifest (including a few stage-anchored params) to prove the **search** changes actually emit and return results. Small and explicitly budgeted, not a full 425-query crawl.

---

## Phasing (reordered so semantics lead)

**Phase 1 — Ontology + contract design (paper/config; no API):**
1. Domain taxonomy; assign all 85 existing parameters to domains (`implementation_status: active`); add a small set of obvious stubs (`implementation_status: stub`) — not 200 active params.
2. Manifest schema deltas (`domain`, `parameter_kind`, `concept_scope`, `decision`, optional `requires_stage_context`, `implementation_status`).
3. **Normalized extraction contract**: claim-schema deltas (`agronomic_scope`, optional `bbch_applicability`, `provenance.manifest_version`; `domain`/`parameter_kind` materialized, **not** stored) + review/promotion semantics for them.
4. `bbch.json` reference + `bbch_stage_map` for the 7 crops.
5. Domain × concept_scope coverage-matrix stub.
6. Schema-evolution decision recorded (optional-fields, no forced migration — see below).
→ **Deliverable:** reviewable ontology + contract artifact. Gate before any code.

**Phase 2 — Wire + prove, in two parts (see "Sequencing trap" above):**
- **2a Re-extraction (no crawl):** implement the Claude extractor against the restructured 85, propagate new fields through normalize → review → promote → coverage, re-extract **wheat from existing captures**, and publish a before/after coverage table (parameters-with-claims, % unmapped, dedup ratio, promoted count) **scored only over parameters that have captures**.
- **2b Fresh-crawl spike:** make the `parameters.py` search touchpoints live (overlay union, `{stage}` expansion, query budget) and run a **small budgeted live wheat crawl** to prove search emits/returns the new query shapes. This is the only step that validates the search changes.

Yield across 2a (extraction) and 2b (search) is the gate for any expansion.

**Phase 3+ — Expand deliberately:**
Only after Phase 2 yield is acceptable: deepen domains, add the typed IPM sub-shape, then economics/post-harvest. New crops are out of scope until the existing 7 are healthy.

---

## Scope decisions (locked)
- **Crops:** existing 7 (corn, wheat, rice, soybean, cotton, sunflower, tomato), restructured. No new crops until they're healthy.
- **Domains:** all 12 represented breadth-first, but **populated incrementally** — stubs first, depth after extraction yield is proven.
- **Parameter count in Phase 1:** restructure 85 + obvious stubs. Explicitly **not** 150–200.

---

## Risks & guardrails
- **Metadata that never reaches claims** → every new manifest field has a defined **materialization path** into review/export — a matching *claim* field **only** when the value is non-derivable and value-specific (`agronomic_scope`, `bbch_applicability`). Derivable fields (`domain`, `parameter_kind`) are materialized from `provenance.manifest_version`, never copied onto claims.
- **Extraction-yield gap repeats at larger scale** → expansion gated on the Phase 2 wheat coverage table.
- **Query explosion** → stage expansion is opt-in per parameter (`requires_stage_context`) + per-run budget with logged truncation.
- **IPM brittleness** → typed sub-shape, deferred; not flattened into generic parameter fields.
- **Field-shape confusion** → parameter `requires_stage_context` (boolean) and claim `bbch_applicability` (object) carry different names *and* shapes so they can't be wired interchangeably.

## Schema evolution & backward compatibility
- **All new claim, durable, and Postgres fields are optional/nullable** — they are **not** added to any `required` list. Existing pre-v2 artifacts stay valid; there is **no forced migration**.
- **Manifest version is the pivot.** Each claim records `provenance.manifest_version`; review/export materialize `domain`/`parameter_kind` from that exact manifest version, so old runs never get mislabeled by a newer manifest.
- **Validation handles historical runs** — because new fields are optional, the additive schema validates both old and new claims; no re-validation of past runs is required.
- **Postgres** — new columns are added nullable with no backfill; the loader writes them only when present.
- **Tests** — existing fixtures stay green (fields optional); add new fixtures exercising `agronomic_scope` and `bbch_applicability`. Old fixtures are *not* edited to carry new fields.

## Acceptance criteria (Phase 2 gate) — computable formulas

Denominators count **active** parameters only (stubs/deferred excluded). Current wheat baseline (measured 2026-06-22 on `pilot-global-wheat-001`, 2,338 normalized claims) is given for each metric so "better" is unambiguous.

**2a — Re-extraction (scored only over parameters that have ≥1 capture):**

| Metric | Formula | Baseline (now) | Phase 2 gate |
|---|---|---|---|
| **Unmapped rate** | `unmapped_claims / total_claims` | 16.1% | **< 10%** |
| **Single-bucket dominance** | `max_parameter_claims / total_claims` | 57.2% (optimum_growth_temp) | **< 25%** (no single parameter dominates) |
| **Duplicate redundancy** | `(total_claims − distinct_claims) / total_claims`, where "distinct" = unique `(claim_text, applicability_key)` | 45.5% (1,064 redundant copies) | **< 5%** |
| **Max identical-sentence repeats** | `max count of any (claim_text, applicability_key)` | 30 | **≤ 2** |
| **Coverage breadth** | `active_params_with_claims / active_params_having_captures` | 14 params had claims | **≥ 60%** |
| **Applicability completeness** | `numeric_claims_with(agronomic_scope OR bbch_applicability OR non-global location) / numeric_claims` | not tracked | **≥ 70%** |
| **Promotion** | `promoted_durable_claims` | 1 | **≥ 10** |

**2b — Fresh-crawl spike (validates the search layer):**
- Stage-anchored (`requires_stage_context`) parameters emit `{stage}`-expanded queries; overlay (`concept_scope: crop`) parameters emit queries.
- ≥ 1 non-empty capture returned for a **representative sample** of both groups (target: ≥ 70% of sampled new parameters return at least one capture).
- The query budget is respected and any truncation is logged (no silent caps).

**Coverage reporting separates the two gaps:** every active parameter is reported as one of `no_captures_crawled` / `captures_but_no_claims` / `claims_present`, so a *search* gap is never misread as an *extraction* failure.
