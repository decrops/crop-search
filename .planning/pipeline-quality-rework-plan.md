# Pipeline Quality Rework: Acquisition → Extraction → Promotion

**Status:** IMPLEMENTED 2026-06-24 (Phases A–E built + tested, 114 tests pass; Phase F proven offline; live re-crawl + Opus pass pending). See §11 Implementation status.
**Date:** 2026-06-24
**Scope:** wheat first (`pilot-global-wheat-001`), then generalize to the other 6 crops.
**Supersedes:** the crawl-side ("everything before Opus") portions of [opus-vault-extraction-plan.md](opus-vault-extraction-plan.md). That plan's renderer + corpus-from-captures stages stay; this plan replaces the *live* acquisition stage and adds query-generation, a seed registry, eval sets, and promotion rework.

---

## 0. Why this plan

The Opus extraction pass works (wheat: 85/85 params, temperature 6.3%, notes in the vault). The bottleneck has moved **upstream and downstream of Opus**:

1. **Upstream — acquisition is not auditable or resumable.** The last wheat run had ~300 source failures, never retried; the raw store records only what *survived* top-N filtering, not the full discovery process; provider 429s killed sources permanently; peer-reviewed full text was mostly left unfetched. Opus can only extract from what the crawl hands it.
2. **Downstream — promotion is heuristic and leaves too much in `needs_review`.** Review/promotion scores claims with keyword cascades ([review.py](../src/crop_search_framework/review.py)) that ignore source tier, applicability scope, value compatibility, and table/section provenance.

There is **no eval harness**, so we cannot tell whether a change to query generation, fetch selection, or promotion actually helped — and we can't separate *retrieval* quality (did we find the right sources?) from *extraction* quality (did we read them right?).

### What already exists (do NOT rebuild)
- [corpus.py](../src/crop_search_framework/corpus.py) — `build-corpus`: content-addressed document store deduped by `text_hash`, block store (sections/paragraphs/tables), `query_hits`, `corpus_manifest.json`; `corpus-qa` with gates. **Consumes captures that already exist.** No live discovery.
- [backfill.py](../src/crop_search_framework/backfill.py) — `http_get_with_retry`, `resolve_oa` (Unpaywall → OpenAlex best-OA), `is_junk_doi`, `backfill_corpus`. Retry/cache/OA logic exists here but only **post-hoc**, and the HTTP helper is **JSON-only** (see WS-3).
- [vault_render.py](../src/crop_search_framework/vault_render.py) — `render-vault`. Unchanged by this plan.
- [wheat.json](../config/crops/wheat.json) already has `scientific_names` and `bbch_stage_map` (see WS-5 — reuse, don't re-add).

This plan's job: turn the one-shot, lossy, fused `run-exploration` into a **resumable acquisition system** feeding the existing corpus builder, **upgrade query generation and promotion**, all gated by **golden eval sets that separate retrieval from extraction**. Much of the retry/OA machinery exists in `backfill.py` — the work is largely **promoting it from a post-hoc patch to a first-class live stage** and adding the ledger/fetch-split around it.

---

## A note on `run-exploration` (resolved: wrap, don't fork)

Today [exploration.py](../src/crop_search_framework/exploration.py) `run-exploration` **fuses** search + selection + fetch + parse + raw writes, and **clears prior raw artifacts on rerun** ([:42](../src/crop_search_framework/exploration.py#L42), [:291](../src/crop_search_framework/exploration.py#L291)). Building `discover`/`select-fetch`/`fetch` alongside it would create **two acquisition systems that drift**.

**Decision:** the new stages are the real pipeline; `run-exploration` becomes a **thin compatibility wrapper** that calls `discover → select-fetch → fetch → build-corpus` in sequence and prints the same summary. The fused/destructive code path is **deprecated and removed** once the wrapper reaches parity (tracked in Phase B2). The corpus store is the only durable raw layer; `run-exploration` no longer clears anything.

---

## 1. Workstreams (mapped to the review comments)

### WS-1 — Discovery ledger (discovery only — no fetch decisions)
**Comment:** *Store every provider result before top-N filtering: provider, rank, score, score reasons, URL, DOI, access status, dropped reason.*

- **Now:** [discovery_connectors.py](../src/crop_search_framework/dev_tools/discovery_connectors.py) returns results that [search_web.py](../src/crop_search_framework/dev_tools/search_web.py#L80) truncates to `max_results_per_query: 3`. Worse, some connectors **already drop rows internally** via `relevance_gate()` ([internet_archive_results:439](../src/crop_search_framework/dev_tools/discovery_connectors.py#L439), [wikipedia_results:523](../src/crop_search_framework/dev_tools/discovery_connectors.py#L523)) — so a ledger built from connector output is **incomplete by construction**.
- **Change:** append one line per provider result to `exploration/discovery/<run>/results.jsonl`. **The ledger records discovery, not fetch decisions** — it carries no `selected` flag (that was the v1 confusion). Fields: `ledger_id` (stable per row), `query`, `parameter_id`, `source_tier`, `provider`, `discovery_rank` (rank within that provider/query response), `score`, `score_components{}`, `source_url`, `canonical_key` (DOI or normalized URL), `doi`, `access_status`, `discovery_drop_reason` (`low_score` | `duplicate` | `relevance_gate` | `""` if it survived into the candidate pool).
- **Relevance filtering moves out of connectors (review fix):** connectors return **raw rows**; `relevance_gate()` no longer drops inside them. The gate becomes a downstream step that stamps `discovery_drop_reason=relevance_gate` on the ledger row but **keeps the row**. This is what makes the ledger a true record of everything considered.
- **Score breakdown without breaking callers (review fix):** do **not** change `score_source_result`'s return type (3 call sites: [quality.py:382](../src/crop_search_framework/quality.py#L382), [search_web.py:61](../src/crop_search_framework/dev_tools/search_web.py#L61), [discovery_connectors.py:543](../src/crop_search_framework/dev_tools/discovery_connectors.py#L543)). Add a sibling `score_source_result_with_components()` returning `(int, components{})`; the existing int API stays stable and can delegate to it.
- **Fetch decisions live entirely in `fetch_queue.jsonl` (WS-2)** with `fetch_selected` + `fetch_skip_reason`.
- **Artifact/CLI:** new `discovery.py` + `discover <run>` CLI (discovery only — writes the ledger, no fetch).
- **Verify:** ledger line count == total **raw** provider rows (incl. ones the old `relevance_gate` would have dropped); every dropped result has a `discovery_drop_reason`; no `selected`/fetch fields in the ledger; existing int callers of `score_source_result` unchanged.

### WS-2 — Fetch selection (the only place selection happens)
**Comment:** *Over-collect via search, then choose a balanced fetch queue by parameter, family, source tier, domain, access status, source uniqueness — don't let three mediocre results crowd out one excellent source.*

- **Now:** selection IS search — top-3-per-query inside `search_web`.
- **Change:** new fetch-selection policy consuming the ledger, emitting `exploration/discovery/<run>/fetch_queue.jsonl`, **one line per unique source** (deduped by `canonical_key`) with `fetch_selected` (bool) and `fetch_skip_reason`. Balancing dimensions: parameter coverage (don't over-serve already-covered params), family, source-tier precedence (from [config/source-tiers/default.json](../config/source-tiers/default.json)), `access_status` (prefer `open_full_text`), source uniqueness, and **tier-aware domain caps**.
- **Preserve many-to-many (review fix):** one source is found by many queries/params. Each fetch-queue row keeps the full association — `ledger_ids[]` (every contributing ledger row) and `parameter_ids[]` — so collapsing duplicates by `canonical_key` does **not** lose which queries/parameters pointed there. This list also seeds the corpus `query_hits` and the retrieval eval (WS-8). The fetch↔ledger relationship is many-to-one via `ledger_ids`, not a lossy `canonical_key` join.
- **Tier-aware domain caps (review fix):** a hard global domain cap would wrongly suppress source families like FAO, AHDB, K-State, or a large open textbook that legitimately cover many parameters. The policy caps **low-confidence / low-tier domains aggressively** but grants **curated institutional domains a higher (or exempt) cap**. Cap tiers + an allowlist of trusted domains live in `config/fetch-policy/default.json` (caps per tier, trusted-domain exemptions, per-param target depth) — auditable, not baked into code.
- **Over-collect knob:** raise `max_results_per_query` in *discovery* (cheap, metadata only); selection stays bounded by policy.
- **Artifact/CLI:** `select-fetch <run>` CLI.
- **Verify:** no low-tier domain exceeds its cap; a trusted institutional domain is allowed past the low-tier cap; a high-score unique OA source is never dropped for ≥2 lower-score same-domain duplicates; param-coverage balancing exercised; every `ledger_id` in a fetch-queue row's `ledger_ids[]` exists in the ledger, and a multi-query source carries all its `parameter_ids[]`.

### WS-3 — HTTP client abstraction: retry / backoff / cache / resume
**Comment:** *Providers need per-provider cache, exponential backoff, deferred retry queues — behave like a resumable acquisition system.* **+ review:** `http_get_with_retry` is JSON-only and returns a minimal cached response lacking `text`/`content`/`headers`/`url`.

- **Now:** connectors are bare `requests.get` ([:100](../src/crop_search_framework/dev_tools/discovery_connectors.py#L100), [:196](../src/crop_search_framework/dev_tools/discovery_connectors.py#L196)); a 429 raises, is caught into an `errors[]` string ([:89](../src/crop_search_framework/dev_tools/discovery_connectors.py#L89)), and the provider's results are silently lost. `http_get_with_retry` in [backfill.py](../src/crop_search_framework/backfill.py#L49) caches only `resp.json()` and its `_CachedResponse` exposes the JSON payload but **no `text`/`content`/`headers`/`url`** — unusable for HTML/PDF fetches or provider calls that need response metadata.
- **Change:** introduce a small **HTTP client abstraction** (`dev_tools/http_client.py`) with two cache modes:
  - **JSON mode** — for discovery APIs; caches parsed JSON (current behavior, generalized).
  - **Binary mode** — for HTML/PDF fetches; caches raw bytes + a sidecar of `status`, `headers`, final `url` (post-redirect), `content_type`. Cached responses round-trip `text`, `content`, `headers`, `url`.
  Both modes share: exponential backoff + retry on 429/5xx, per-provider/per-host on-disk cache keyed by `method+url+normalized-params`, and a **deferred-retry queue** (`exploration/discovery/<run>/retry_queue.jsonl`) re-attempted on the next `discover`/`fetch` invocation (resume). Route **all** connectors and the fetcher through it; migrate `backfill.py` to the shared client.
- **Artifact/CLI:** `dev_tools/http_client.py`; `discover --resume` / `fetch --resume`; cache under `exploration/cache/<host>/`.
- **Verify:** backoff on stubbed 429-then-200; JSON-mode and binary-mode cache hits both avoid a second call; binary cache round-trips `text`/`headers`/`url`; deferred-retry queue drains on resume; transient failures no longer drop results permanently.

### WS-4 — Open-access scholarly retrieval (aggressive, legal)
**Comment:** *Resolve legal full text harder: OpenAlex OA locations, Europe PMC XML/PDF, DOAJ fulltext, Crossref license/link, publisher PDFs when open. Keep paywalled metadata-only.*

- **Now:** `resolve_oa` (Unpaywall → OpenAlex best-OA) runs only post-hoc. **Crossref `link`/`license`/ISSN/container-title are NOT captured** — [crossref_results](../src/crop_search_framework/dev_tools/discovery_connectors.py#L152) stores only `doi`, `publication_year`, `type`, `publisher`. So OA filtering on Crossref link/license is impossible today.
- **Change (two parts):**
  1. **Capture the metadata first.** Extend `crossref_results` to store `link[]` (URL + content-type + intended-application), `license[]`, `ISSN`/`container-title`, and keep `type` (used by the `journal-article` filter below). Without this, WS-4's Crossref path has nothing to act on.
  2. **Promote OA resolution into the fetch stage.** For each scholarly candidate in the fetch queue, resolve a legal full-text URL in priority order: OpenAlex `best_oa_location.pdf_url`/`oa_url` → Europe PMC `fullTextUrlList` (XML preferred, then PDF) → DOAJ `link[type=fulltext]` → Crossref `link` filtered by `license` + `content-type` → Internet Archive file list. Record `oa_resolution_method` + `oa_license` on the document. Paywalled (no OA location) stays `metadata_only` and is **not** fetched.
- **Type filtering at selection — allowlist, not a blunt `journal-article` rule (review fix):** a hard `type == journal-article` filter would drop reviews and meta-analyses, which are often the **best** source for physiology parameters. Instead use an **article-like allowlist** (`journal-article`, `review`, `proceedings-article`, `book-chapter`, `posted-content`/preprint) combined with `is_junk_doi` (datasets, supplements, errata, indexes). Reviews/meta-analyses are **kept but ranked differently** in WS-9 promotion (secondary-synthesis vs primary measurement), not excluded. This still removes the ~70% non-article Crossref noise from the last run without throwing away useful syntheses.
- **Artifact/CLI:** Crossref metadata extension; OA resolver in the selection/fetch path (reuse + extend `resolve_oa`); `--email` for the polite Unpaywall/Crossref pool.
- **Verify:** Crossref records now carry link/license/ISSN; golden DOIs with known OA copies resolve to a fetchable URL ≥80%; paywalled stay metadata-only; junk/non-article DOIs excluded.

### WS-5 — Parameter-aware query generation
**Comment:** *Use parameter-specific units, stage terms, scientific names, source-tier templates. Nutrients → kg/ha, lb/ac, N rate, split application; phenology → BBCH, Zadoks, Feekes; water → Kc, ETc, depletion. Avoid one generic query shape.*

- **Now:** [build_query](../src/crop_search_framework/parameters.py#L129) appends generic tier terms + literally the word `"value"` for numeric params. One shape for all 85 params; `evidence_patterns` carry no units/stage vocab/scientific names.
- **Change:**
  - Add to the manifest (additive, optional): `query_units` (e.g. `["kg/ha","lb/ac"]`), `query_terms` (domain idiom — nutrients → `["N rate","split application","topdress"]`; phenology → `["BBCH","Zadoks","Feekes"]`; water → `["Kc","ETc","crop coefficient","allowable depletion"]`); reuse the existing `domain` field for domain-level templates.
  - **Scientific names (review fix): reuse, don't re-add.** [wheat.json](../config/crops/wheat.json#L10) already has `scientific_names`. Emit a scientific-name query variant for scholarly tiers from the existing field; **add the field only to crop profiles that lack it.**
  - **Source-tier query templates:** scholarly tiers get scientific-name + unit + method vocab; extension/industry tiers get common-name + region + practical idiom. Template set in `config/query-templates/default.json`.
  - Replace the blunt `"value"` token with the param's `query_units`.
  - Stage-expanded queries only for params flagged `requires_stage_context`, under the existing query budget; reuse `bbch_stage_map` for stage vocabulary where present.
- **Artifact/CLI:** extend [parameters.py](../src/crop_search_framework/parameters.py) `build_query`/`generate_parameter_queries`; new template config; manifest schema deltas (optional fields).
- **Verify:** snapshot tests per domain (nutrient query contains a unit + N-rate idiom; phenology contains BBCH/Zadoks; water contains Kc/ETc; scholarly-tier query contains the scientific name). Real lift measured by the **retrieval** gold set (WS-8).

### WS-6 — Curated seed registry
**Comment:** *Move seeds out of run configs into a reusable registry with verified scope, covered parameters, crop, region, tier, source quality, known caveats. The UNL seed mismatch is exactly why.*

- **Now:** seeds are inline in each run config ([pilot-global-wheat.json:28-154](../config/runs/pilot-global-wheat.json#L28)) — 10 wheat seeds with `parameter_ids` but no verified-scope/quality/caveat metadata, duplicated across runs, drift-prone (the UNL G2122 mismatch).
- **Change:** new `config/seeds/<crop>.json` registry. Each seed: `seed_id`, `source_url`, `crop`, `region`, `source_tier_id`, `covered_parameters[]`, `verified_scope` (human-checked content), `source_quality`, `last_verified`, `caveats[]` (e.g. "UNL G2122 is N/P/S only — not seeding rate"), `access_status`. Run configs reference seeds by `seed_id` + a filter (crop/region/tier), not inline blobs.
- **Artifact/CLI:** registry schema + `seeds-validate` CLI (dead-link check via the WS-3 client, schema validation, parameter-id existence against the manifest). Migrate the 10 wheat seeds out of the run config.
- **Verify:** schema validation; every `covered_parameters` id exists in the manifest; `seeds-validate` flags dead URLs and scope mismatches.

### WS-7 — Richer raw structure + immutability + extraction reads blocks
**Comment:** *Extend the block store to PDFs (pages, headings, tables, captions, row/column structure, offsets, anchors). Make raw storage immutable with a corpus manifest (query-plan hash, manifest version, tier-policy hash, parser version, content/provider versions, timestamp).* **+ review:** extraction still reads `candidate_claims`, not blocks.

- **Now:** [parse_document.py:206](../src/crop_search_framework/dev_tools/parse_document.py#L206) caps candidate claims at 8 and filters tables. `corpus.py` builds a block store, but from already-parsed text — PDF table row/column structure and page numbers are lost at parse time. Critically, **extraction input bypasses blocks**: [capture_input_text](../src/crop_search_framework/llm_extract.py#L154) prefers `candidate_claims` (falling back to `raw_text`) and caps with `MAX_INPUT_CLAIMS`/`MAX_INPUT_CHARS`. `run-exploration` clears raw on rerun.
- **Change (three parts):**
  1. **Parser:** upgrade [parse_document.py](../src/crop_search_framework/dev_tools/parse_document.py) to emit structured blocks at parse time for PDFs — page numbers, heading hierarchy, **tables with row/column cells + caption**, char offsets, stable `block_anchor`s. Remove the 8-candidate cap.
  2. **Extraction reads blocks (review fix):** change the extraction input path so Opus consumes the **corpus document blob + block store** (sections/paragraphs/tables) directly, **not** the raw capture's `candidate_claims`. `capture_input_text` is replaced/bypassed by a block-aware input builder keyed by `document_id`; `MAX_INPUT_*` caps are reframed as per-document budgets over blocks, not a truncation of pre-extracted candidates. This is the change that lets table-bound nutrient/harvest values reach Opus.
  2b. **Block provenance flows into claims (review fix — WS-9 depends on this).** Today `EXTRACTION_KEYS` ([llm_extract.py:28](../src/crop_search_framework/llm_extract.py#L28)) carries no provenance anchors, so promotion has no reliable signal for "this came from a table." Extend the **extraction contract** with `document_id`, `block_anchor`, `block_type` (`paragraph`|`table`|`heading`), `page`, and `table_label` (when the cited block is a table). The block-aware input builder presents each block with its anchor so Opus can cite which block a value came from. Carry these through `_claim_from_extraction` into the **normalized claim `provenance`** (additive/optional fields — old artifacts stay valid). Only once these are on the claim can WS-9 reward table/section provenance and the extraction gold set check evidence-faithfulness against a specific block.
  3. **Immutability:** the corpus store is already content-addressed/write-once. Harden the manifest to record `query_plan_hash`, `manifest_version`, `source_tier_policy_hash`, `fetch_policy_hash`, `parser_version`, per-document `content_hash` + `text_hash`, provider versions, and a run timestamp via `datetime.now(timezone.utc).isoformat()` (review fix — not "Date.now"). A rerun writes a **new manifest version**, never overwrites blobs.
- **Artifact/CLI:** parser version bump; block-aware extraction input; extend `corpus_manifest.json`; block schema gains `page`, `table.cells[][]`, `caption`, `block_anchor`; extraction-contract + normalized-claim `provenance` schema deltas (`document_id`, `block_anchor`, `block_type`, `page`, `table_label`).
- **Verify:** golden PDF (K-State handbook) → tables extracted with correct row/col + page numbers; extraction input for a doc contains its table blocks; a normalized claim carries `block_anchor`/`block_type` resolving back to a real block; block anchors stable across reruns; rerun produces a new manifest version without clobbering blobs.

### WS-8 — Evaluation: retrieval gold set + extraction gold set
**Comment:** *Golden set per domain; score precision, recall, parameter mapping, unit normalization, evidence faithfulness before scaling.* **+ review:** query/retrieval changes need a **retrieval** gold set too — claim-from-document eval won't prove WS-5 found better sources.

- **Now:** none.
- **Change — two gold sets:**
  1. **Retrieval gold set** (`tests/golden/retrieval/<domain>/`): for each parameter/domain, the expected authoritative source(s) — `expected_url`/`doi`/`domain`. `eval-retrieval <run>` checks that each expected source appears in the **discovery ledger** and, preferably, in the **fetch queue** (selected). Metrics: per-domain **source recall** (expected sources found in ledger), **fetch-selection recall** (expected sources selected), and **rank** of the expected source. This is what proves WS-5/WS-2 lift. **Note:** scoring requires a ledger to exist — the gold set + scorer are built in Phase A, but the first scored run is the B1 baseline (see Phasing).
  2. **Extraction gold set** (`tests/golden/extraction/<domain>/`): hand-labeled expected claims for known wheat documents (FAO water table, K-State nutrient tables). Each record: `document_id`, `parameter_id`, expected value/range/unit, `evidence_text` span. `eval-extraction <run>` scores **precision/recall**, **parameter-mapping accuracy**, **unit-normalization correctness**, **evidence faithfulness** (cited span supports the value — the F/inches/lb-acre verbatim issue). Output `exploration/eval/<run>/scorecard.{json,md}`.
- **Artifact/CLI:** `eval-retrieval` + `eval-extraction`; golden fixtures. `eval-extraction` runs offline against cached Opus extractions (no spend); `eval-retrieval` runs against a discovery ledger (existing extraction cache for extraction; first real ledger from B1 for retrieval).
- **Verify:** both scorecards compute their metrics; per-domain thresholds set from the Phase-A baseline; these are the regression gates for any acquisition/query/promotion change. **Run before scaling to a new crop.**

### WS-9 — Stronger promotion logic
**Comment:** *Promotion should use source-tier precedence, applicability scope, value compatibility, evidence specificity, and table/section quality — too much is left in needs_review.*

- **Now:** [review.py](../src/crop_search_framework/review.py) `score_claim`/`decide_promotion` use keyword artifact patterns + extractor confidence + value-type. It ignores the corpus source tier, applicability scope, value compatibility (overlapping ranges), evidence specificity, and table provenance. [promote.py](../src/crop_search_framework/promote.py) just copies durable decisions filtered by `conflict_status`.
- **Change:** rework `decide_promotion`/`score_claim`:
  - **Source-tier precedence:** use the document's `source_tier` (now durable on the corpus doc) — peer-reviewed/extension outrank industry/Wikipedia for the same param+scope, replacing the domain-substring heuristic. Also distinguish **primary measurement vs secondary synthesis** (the reviews/meta-analyses WS-4 keeps): syntheses are good corroboration but a primary measurement with a specific value+unit wins for canonical promotion.
  - **Applicability scope:** rank universal/crop-group claims higher for canonical promotion; don't reject a regional claim for lacking universality.
  - **Value compatibility:** before flagging `conflict_status: potential`, check whether numeric ranges **overlap** — overlapping ranges from different tiers **merge**, not block. Main lever to drain `needs_review`.
  - **Evidence specificity:** reward claims whose `evidence_text` has a unit + number close together; penalize vague prose.
  - **Table/section quality:** reward claims sourced from a `table` block with caption/header (from WS-7) over loose-paragraph claims.
- **Artifact/CLI:** extend [review.py](../src/crop_search_framework/review.py) + [promote.py](../src/crop_search_framework/promote.py); thresholds tuned against the extraction gold set (WS-8) and the wheat baseline.
- **Verify:** unit tests for overlap-merge vs true-conflict; tier-precedence ordering; wheat `needs_review` share drops materially while golden-set precision does not regress.

---

## 2. Phasing (revised per review)

Reordered so query quality improves **before** the full refetch, and new acquisition stages aren't built around old generic queries.

- **Phase A — Gold sets + extraction baseline (WS-8).** Build **both** gold sets (retrieval + extraction). Run `eval-extraction` now against existing **cached Opus extractions** (those exist) for the extraction baseline. **Retrieval scoring cannot run yet** — the new discovery ledger doesn't exist (review fix). Two options, do whichever is cheaper: (a) backfill a **one-off baseline ledger** from the existing raw/search artifacts so `eval-retrieval` has something to score, or (b) defer the first `eval-retrieval` run to the end of B1. Plan of record: build the gold sets + `eval-retrieval`/`eval-extraction` code in A; run the **retrieval baseline after B1** once a real ledger exists.
- **Phase B1 — Discovery ledger + HTTP client (WS-1, WS-3).** Ledger (discovery only, relevance-gate moved out of connectors) + the JSON/binary HTTP client with retry/cache/resume. `discover` CLI. Prove ledger completeness; re-discover wheat and compare against the last run's failures. **Run the retrieval-eval baseline here** against the first real ledger. *No fetch selection yet.*
- **Phase C — Query templates + seed registry (WS-5, WS-6).** Parameter-aware queries + curated seeds feed B1's discovery. **Re-run `eval-retrieval` and compare to the B1 baseline** to prove query lift before investing in a full refetch.
- **Phase B2 — Fetch selection + OA resolver (WS-2, WS-4).** Built on the *improved* discovery outputs from C. Crossref metadata extension, tier-aware caps, OA resolution. `select-fetch` + `fetch`; retire `run-exploration` into the compatibility wrapper here once at parity.
- **Phase D — Richer raw + immutability + block-fed extraction (WS-7).** Parser upgrade, manifest hardening, extraction reads blocks. Re-build corpus; confirm table extraction + block-fed input on golden PDFs.
- **Phase E — Promotion rework (WS-9).** Tune against the extraction gold set; show `needs_review` drop without precision regression.
- **Phase F — Full wheat end-to-end** (discover → select → fetch → corpus → corpus-qa → Opus → normalize → review → promote → render-vault), gated by corpus-QA + both scorecards. Then generalize to the other 6 crops one at a time, each gated by corpus-QA + a per-crop golden spot-check.

---

## 3. Acceptance gates

**Acquisition (vs last wheat run: ~300 failures, 149 metadata-only):**

| Gate | Target |
|---|---|
| Discovery ledger completeness | every provider result recorded with a `discovery_drop_reason`; ledger carries no fetch flags |
| Fetch queue / ledger reconciliation | `fetch_selected` candidates all join to a ledger row on `canonical_key` |
| High-tier fetch failures after retry/resume | < 10% of **selected, non-paywalled, high-tier fetch candidates post-retry** (explicit denominator — not all discovered high-tier URLs, since paywalled/metadata-only are never fetched) |
| OA full text fetched where an OA location exists | ≥ 80% |
| Metadata-only share of Opus input set | < 15% |
| Domain dominance | no low-tier domain > its cap; trusted institutional domains exempt/raised |
| Resumability | re-run skips cached/completed work; deferred-retry queue drains; binary cache round-trips text/headers/url |

**Retrieval (WS-8 retrieval gold set):** per-domain source recall (expected source in ledger) and fetch-selection recall (selected); **measured before vs after WS-5** to prove query lift.

**Extraction (WS-8 extraction gold set) — per domain:** precision, recall, parameter-mapping accuracy, unit-normalization correctness, evidence faithfulness; thresholds from the Phase-A baseline; **no regression** on later changes.

**Promotion (WS-9):** `needs_review` share drops vs baseline; canonical-candidate count rises; golden-set precision holds; overlapping-range conflicts merge instead of blocking.

---

## 4. Open / deferred

- **Concurrency/politeness** for the resumable fetcher — start conservative, single-threaded with backoff.
- **Cross-run document dedup** (same DOI across crops) — content-hash/DOI keyed; one corpus doc, many `query_hits`.
- **PDF table extraction library** (WS-7) — evaluate `pdfplumber`/`camelot` vs the current parser; pick by golden-PDF table accuracy.
- **Eval-set growth** — start tiny (a few docs/domain); expand as crops are added.
- **Non-wheat seed curation** (WS-6) — registry built for wheat first, other crops in Phase F.

---

## 11. Implementation status (2026-06-24)

Built and tested (114 unit tests pass; baseline was 73). New CLIs: `discover`, `select-fetch`, `seeds-validate`, `eval-extraction`, `eval-retrieval`.

| WS | Status | Key code |
|---|---|---|
| WS-1 discovery ledger | ✅ | `discovery.py` → `results.jsonl` (every raw row, `discovery_rank`, `score_components`, `discovery_drop_reason`; no fetch flags). `relevance_gate` removed from connectors → downstream stamp. `quality.score_source_result_with_components` (int API preserved). |
| WS-2 fetch selection | ✅ | `fetch_selection.py` → `fetch_queue.jsonl` (`fetch_selected`/`fetch_skip_reason`, `ledger_ids[]`+`parameter_ids[]` many-to-many), tier-aware domain caps + trusted allowlist; `config/fetch-policy/default.json`. |
| WS-3 HTTP client | ✅ | `dev_tools/http_client.py` (JSON+binary modes, backoff/retry, on-disk cache, round-trips text/headers/url). All connectors routed through `_get_json`. Deferred-retry queue + `--resume`. |
| WS-4 OA + Crossref meta | ✅ | `crossref_results` now captures link/license/ISSN/container/type; OA resolution hook in `select-fetch --resolve-oa` (reuses `backfill.resolve_oa`); article-like allowlist + `is_junk_doi`. |
| WS-5 query generation | ✅ | `parameters.build_query` + `config/query-templates/default.json` (units/idiom/stage vocab); scientific names for scholarly tiers (reused from crop profiles); blunt `value` token replaced by units. |
| WS-6 seed registry | ✅ | `config/seeds/wheat.json` + `schemas/seed-registry.schema.json` + `seeds.py` (`seeds_for_run`, `validate_seeds`); run config now references registry; `seeds-validate` CLI. |
| WS-7 blocks/immutable/block-fed | ◑ | Block-provenance contract (`document_id`/`block_anchor`/`block_type`/`page`/`table_label`) in `EXTRACTION_KEYS` + normalized `provenance`; block-fed extraction input (`render_blocks`); hardened `corpus_manifest` (content hash, revision, policy hashes, ISO timestamp). **Deferred:** swapping the PDF parser to pdfplumber/camelot for true table cells/pages at parse time (needs new dep + live PDFs). |
| WS-8 eval | ✅ | `eval_harness.py` (`eval-extraction` + `eval-retrieval`); gold sets in `tests/golden/{extraction,retrieval}/`. Extraction baseline: recall 1.0, **precision 0.65** (adjudicable), unit 1.0, evidence-faithfulness 0.75. Retrieval scored after a ledger exists (B1). |
| WS-9 promotion | ✅ | `review.py`: source-tier precedence (`SOURCE_TIER_WEIGHTS`, canonical bar 66 for high-trust tiers), table-block + evidence-specificity bonuses, secondary-synthesis never canonical. Range-overlap already merges (not blocks) via `quantitative_values_compatible`. |

**run-exploration:** marked DEPRECATED in CLI help; superseded by `discover → select-fetch → fetch → build-corpus`.

**Fetch executor (2026-06-24, after initial build):** `fetch_stage.py` + `fetch` CLI closes the last connector-side gap — consumes `fetch_queue.jsonl` + the ledger, fetches selected URLs via the binary `HttpClient` (cache/backoff/resume; uses `resolved_oa_url`), parses with `parse_document`, and writes raw captures (one per (doc, parameter_id), queries recovered from the ledger so `query_hits` keep the many-to-many). Paywalled rows → metadata-only captures. `--resume`/`--limit`. The reworked path is now genuinely end-to-end: `discover → select-fetch → fetch → build-corpus → corpus-qa → (Opus) → normalize → review → promote → render-vault`. 2 new tests incl. captures→build-corpus (116 total).

**Phase F (offline proof on existing wheat artifacts):** `build-corpus` → hardened manifest (revision 2, content hash, policy hashes); `corpus-qa` gates correctly (metadata-only 17.9% pre-backfill); `review-run`+`promote-run` on `pilot-global-wheat-opus` → **52 durable claims promoted** (19 canonical + 33 regional), up from ~1 historically. Tier-precedence is neutral on this *legacy* dataset (base scores already clear 72; `block_type` empty pre-Phase-D) — mechanism is unit-tested and activates on future block-extracted runs.

**Not yet run (needs network + Opus spend, user-gated):** a live `discover`/`select-fetch` crawl producing a real ledger (which would let `eval-retrieval` score query lift), and a fresh full-wheat Opus pass through the hardened pipeline into the vault.
