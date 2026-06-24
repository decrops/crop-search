# Reworked Pipeline: Durable Raw Corpus → Opus Extraction → Tagged Obsidian Vault Notes

**Status:** Plan v2 (revised after raw-quality review; awaiting build approval)
**Date:** 2026-06-23
**Run in scope:** wheat (`pilot-global-wheat-001` corpus as the seed input)
**Decided with the user (2026-06-23):**

| Decision | Choice |
|---|---|
| Note granularity | **Atomic** — one note per crop × parameter ("data-point note") |
| Output location | **Directly into the vault**: `Tino_deCrops/DeCropsResearch/crop_science/` |
| Tagging model | **YAML frontmatter `tags:` (facets) + `[[wikilinks]]` (entity graph)** |
| First-build scope | **Wheat** |

Vault absolute path:
`/Users/admin/Library/Mobile Documents/iCloud~md~obsidian/Documents/Tino_deCrops/DeCropsResearch/crop_science/`

---

## 0. What changed in v2 (review response)

The v1 plan claimed the crawl was "healthy" and that extraction was the *only* bottleneck. The review showed that's wrong: **raw discovery/storage is still a major bottleneck**, and feeding today's captures to an expensive Opus pass blindly would waste tokens on duplicates, metadata-only stubs, and lossy parsing. v2 puts a **durable, deduplicated, auditable raw corpus** ahead of Opus, gated by a QA report.

| Review finding | Severity | Change in v2 |
|---|---|---|
| "Crawl is healthy" overstated — 825 captures = only **356 unique URLs / 280 unique texts**, 300 failures, 149 metadata-only, 157 Wikipedia | High | §1 reframed: raw quality is a co-equal bottleneck. Honest corpus metrics replace the "healthy" claim. |
| "No re-crawl" risky for full wheat | High | New **Phase 1.5 corpus audit/backfill** before full Opus; "no re-crawl" kept only for the small proof. |
| Fan-out unit should be **unique documents + query/parameter associations**, not 825 captures (current dedupe is `(source_url, parameter_id)`, [exploration.py:124](/Users/admin/dev/crop-search/src/crop_search_framework/exploration.py#L124)) | High | New **document registry** (`document_id`, canonical URL/DOI, content hash, text hash) + separate **`query_hits`** linking doc→parameter/tier/query. Opus runs per unique document. |
| Raw store not durable — `run-exploration` clears raw + fetched artifacts on rerun ([exploration.py:42](/Users/admin/dev/crop-search/src/crop_search_framework/exploration.py#L42), [:291](/Users/admin/dev/crop-search/src/crop_search_framework/exploration.py#L291)) | High | **Immutable, content-addressed corpus store** + `corpus_manifest.json` snapshot (query plan, manifest version, parser version, provider responses, fetch headers/status, redirect chain, hashes). Never cleared. |
| Store the whole **search ledger**, not only selected captures (search dedupes top-N, [search_web.py:80](/Users/admin/dev/crop-search/src/crop_search_framework/dev_tools/search_web.py#L80); `max_results_per_query: 3`) | Medium | New **discovery ledger** `exploration/discovery/<run>/results.jsonl` (provider, rank, score + components, access status, dedupe key, dropped reason). Fetch selection becomes a separate auditable **ranked fetch queue** policy. |
| Provider **retry/backoff/caching** in scope (429s; direct calls at [discovery_connectors.py:100](/Users/admin/dev/crop-search/src/crop_search_framework/dev_tools/discovery_connectors.py#L100), [:196](/Users/admin/dev/crop-search/src/crop_search_framework/dev_tools/discovery_connectors.py#L196)) | Medium | Per-provider **cache + exponential backoff + resume + deferred-retry queue**. |
| Raw parsing too lossy — Opus gets `raw_text` + `candidate_claims` capped at 8, tables filtered ([parse_document.py:206](/Users/admin/dev/crop-search/src/crop_search_framework/dev_tools/parse_document.py#L206)) | Medium | New **parsed block store**: `sections`, `paragraphs`, `tables`, page/section anchors, offsets, labels. Opus reasons over blocks (esp. nutrient/harvest tables). |
| Peer-reviewed needs a **full-text retrieval path**, not just metadata | Medium | New **OA resolver**: OpenAlex best-OA location, Europe PMC full-text XML/PDF, DOAJ fulltext, Crossref license/link, Internet Archive file lists. Paywalled stays metadata-only; open full text fetched aggressively. |

---

## 1. Honest state of the raw corpus (replaces the "healthy" claim)

| Metric | Value |
|---|---|
| Captures (`(url,param)` associations) | 825 |
| **Unique URLs** | ~356 |
| **Unique parsed texts** | ~280 |
| Source/fetch failures | 300 |
| Metadata-only (no full text) | 149 |
| Wikipedia/background captures | 157 |

So the *effective* corpus is ~280 unique documents, ~half of them background or thin, with hundreds of failed/rate-limited fetches never retried and peer-reviewed full text mostly unfetched. **Discovery and storage are the first thing to fix** — Opus quality is capped by corpus quality.

---

## 2. Target pipeline (Stage 1 replaced)

```
query plan
  → discovery ledger          (every provider result, ranked, scored, with drop reasons)
  → ranked fetch queue         (auditable selection policy, not baked into search)
  → immutable document store   (content-addressed; corpus_manifest.json snapshot; never cleared)
  → parsed block store         (sections / paragraphs / tables / anchors / offsets / labels)
  → raw corpus QA report       (GATES full Opus)
  → Opus extraction            (per unique document; query_hits give context)
  → normalize / dedup / merge  (reuse existing FixtureBackend replay + normalize-run --from-llm)
  → render-vault               (atomic crop×parameter notes + entity hubs + MOCs)
  → vault QA                   (coverage, orphans, broken links)
```

Stages from normalize onward are unchanged from v1. The rework is everything **before** Opus, plus the renderer.

---

## 3. New durable raw layer (the core of v2)

### 3a. Discovery ledger — `exploration/discovery/<run>/results.jsonl`
One line per provider result for every query (not just the top-3 kept). Fields: `query`, `parameter_id`, `source_tier`, `provider`, `rank`, `score`, `score_components{}`, `access_status`, `dedupe_key` (canonical URL/DOI), `dropped_reason` (e.g. `below_max_results`, `low_score`, `duplicate`). Makes "why was this source/not fetched" auditable.

### 3b. Ranked fetch queue — separate, auditable selection policy
Consumes the ledger, ranks candidates per `(parameter, tier)`, and emits a fetch plan. Selection criteria (tier trust, score, access_status, full-text availability, de-prioritize background/Wikipedia) live in one policy file, not inside `search_web`. Records *all* candidates and *why* each was fetched or skipped.

### 3c. Immutable document store — `exploration/corpus/<run>/`
- `corpus_manifest.json` — run snapshot: query-plan snapshot, `manifest_version`, `parser_version`, provider versions, timestamps, and an index of document hashes. Regenerable, not "hoped for."
- `documents/<document_id>.json` — one record per **unique document**: `document_id` (hash-derived), `canonical_url`, `doi`, `content_hash`, `text_hash`, fetch metadata (`status`, response `headers`, `redirect_chain`, `content_type`, `fetched_at`), `access_status`, `source_tier`, `domain`, `parser_version`, provider provenance.
- `documents/blobs/<content_hash>.<ext>` — the raw fetched bytes, **content-addressed and write-once**.
- `query_hits.jsonl` — associations: `{document_id, parameter_id, source_tier, query, rank, score}`. Many hits → one document. This replaces the `(source_url, parameter_id)` capture explosion.

**Immutability/durability:** the corpus is write-once and never cleared by a rerun. Re-runs and backfills write new/updated document records and a new `corpus_manifest` version; the Opus path reads a pinned snapshot. (Fixes the clearing at [exploration.py:42](/Users/admin/dev/crop-search/src/crop_search_framework/exploration.py#L42)/[:291](/Users/admin/dev/crop-search/src/crop_search_framework/exploration.py#L291) for the corpus path.)

### 3d. Parsed block store — `exploration/corpus/<run>/blocks/<document_id>.json`
Replaces the lossy `raw_text` + 8-`candidate_claims` cap. Structured: `sections[]` (heading, level, anchor), `paragraphs[]` (text, char offset, section ref), `tables[]` (caption, header row, rows, anchor), `labels[]` (e.g. `nutrient_table`, `growth_stage_table`), `parser_version`. Opus can then target tables for nutrients/harvest thresholds and methods — the data the heuristic and the capped parser drop today.

### 3e. Provider hardening — cache + backoff + resume + deferred retry
- Per-provider response **cache** keyed by `provider+query` so backfills/reruns don't re-hit APIs.
- **Exponential backoff** + retry on 429/5xx; a **deferred-retry queue** for providers that rate-limit (Google Books/OpenAlex were the main 429 sources).
- **Resume support**: a run can stop and continue from the queue without re-fetching completed documents.

### 3f. Open-access full-text resolver
Before fetch, resolve each peer-reviewed/textbook candidate to open full text: OpenAlex `best_oa_location`/`oa_url`, Europe PMC `fullTextUrlList` (XML/PDF), DOAJ fulltext links, Crossref `license`/`link`, Internet Archive file lists (where legal). Fetch open full text aggressively; keep paywalled records `metadata_only`. Turns the 149 metadata-only stubs into real text where legally possible.

### 3g. Raw corpus QA report — `exploration/corpus/<run>/qa_report.{json,md}` (GATES full Opus)
Must pass thresholds before Phase 3. Includes: unique document count, **duplicate text ratio** (text_hash collisions), failed fetches by tier, metadata-only count, **Wikipedia/background share**, text-length anomalies (boilerplate/too-short), **table/section coverage**, source-tier and domain coverage, and a **high-value retry queue** (failed/missing high-value queries to backfill). The report is the go/no-go for the expensive Opus pass.

---

## 4. Stage 2 — Opus extraction (now per unique document)

**Unit of work = unique document** (deduped by `text_hash`), not 825 captures — roughly **~280 docs, not 825**, cutting redundant Opus calls by ~3×. Each document is sent once with its **block store** (sections/paragraphs/tables) and its **`query_hits`** (which parameters/tiers/queries pointed here) so Opus knows what's expected but extracts *every* in-scope parameter present.

Orchestration: subagent fan-out (Task tool), one batch of unique docs per agent, each given the 85-id active-parameter vocabulary + the contract below. Results written to `exploration/llm_cache/<run>/<document_id>.json` (`{"claims":[...]}`), which the existing `FixtureBackend` replays into `normalize-run --from-llm --backend fixture` untouched.

**Extraction contract (extends `EXTRACTION_KEYS`):** `parameter_id` (enum to 85 active ids or `none`), value fields (`value_type`, numeric/range, `unit`), `qualifier`, `evidence_text` + **block anchor/offset**, `claim_summary`, `extraction_confidence`, `cultivar`, `management_system`, `bbch_min/max`, plus new optional **`methods[]`** and **`organisms[]`** (pests/diseases/weeds, with role) for tagging. New fields are additive/optional — old artifacts stay valid.

**Cost control:** dedup-by-text first; Phase 2 proof (~40 docs) before the full ~280-doc spend; QA report gates Phase 3.

---

## 5. Stage 4 — markdown renderer (`vault_render.py` + CLI `render-vault`) — unchanged from v1

Deterministic code (Opus = judgment, code = rendering). Three note kinds, written only under `DeCropsResearch/crop_science/`:

- **Data-point note** — one per (crop × parameter): frontmatter (`crop`, `parameter`, `domain`, `parameter_kind`, `value_summary`, `unit`, `confidence`, `source_count`, `source_tiers`, `bbch`, `run_id`, `manifest_version`, `generated_by: crop-search`, faceted `tags:`) + body with consensus value, `[[wikilinks]]`, a sourced-values table (now citing **document_id + block anchor**), evidence quotes, and related links.
- **Entity hubs** — crop / parameter / domain / method / organism / BBCH-stage / **source** notes (the graph backbone; source notes now carry DOI, OA status, content hash).
- **MOCs** — `DeCrops Research — Crop Science (Index).md` and per-crop `Wheat (Index).md`.

**Tag taxonomy:** `crop/ domain/ param/ kind/ method/ pest/ disease/ weed/ stage/bbch-<n>/ source-tier/ confidence/`.

**Vault safety:** only under `crop_science/`; every file marked `generated_by: crop-search`; renderer overwrites **only** its own marked files; `--dry-run` first; `--prune` opt-in for stale generated notes. Existing vault notes (`trait_research`, `GEM_model`, …) are never touched.

---

## 6. Phasing (revised)

- **Phase 0 — this plan.**
- **Phase 1 — Build the durable raw layer + renderer (offline; no Opus, no vault writes):**
  - Discovery ledger + ranked-fetch-queue policy; document registry + immutable content-addressed store + `corpus_manifest`; parsed block store; provider cache/backoff/resume/deferred-retry; OA full-text resolver; corpus QA report generator.
  - `vault_render.py` + `render-vault` CLI; optional schema fields (`methods`, `organisms`, block anchors).
  - CLIs: `discover`, `build-corpus`, `corpus-qa`, `render-vault`. Unit tests (registry dedupe by hash, ledger completeness, immutability/no-clobber, block parsing incl. tables, backoff/retry, frontmatter/tag/link validity, marker-guarded overwrite).
- **Phase 1.5 — Wheat corpus audit + backfill:** run the new discovery/storage over wheat; migrate reusable fetched artifacts; **refetch 429/5xx gaps**; resolve OA full text for peer-reviewed; replace/deprioritize low-value Wikipedia/background; top up weak domains from the high-value retry queue. Produce the QA report.
- **Phase 2 — Proof (~40 unique high-value docs):** Opus → cache → normalize → `render-vault --dry-run` then write; eyeball ~10 notes (values, tags, links, methods/pests, table-derived facts).
- **Phase 3 — Full wheat (GATED by the QA report passing §7 thresholds):** Opus over all unique docs → normalize → render full vault set + entity hubs + MOCs.
- **Phase 4 — Vault QA:** coverage (params/domains with notes), orphan/broken-link check, tag index, temperature-dominance metric.
- **Phase 5 — (future) other crops** (corn, rice, soybean, cotton, sunflower, tomato): same hardened pipeline, re-crawl each.

---

## 7. Acceptance gates

**Corpus QA gates (must pass before full Opus, Phase 3):**

| Gate | Target |
|---|---|
| Unique documents (post text-hash dedup) | counted & reported; Opus runs on unique set only |
| Duplicate text ratio | **< 10%** of the Opus input set |
| Failed fetches in high-value tiers (peer-reviewed/extension/institution) after backfill | **< 10%** |
| Metadata-only share of the Opus input set | **< 15%** (open full text fetched where available) |
| Wikipedia/background share of the Opus input set | **< 15%** |
| Table/section coverage | tables extracted & block-stored for ≥1 doc per nutrient/harvest/stress domain |
| OA full text fetched for peer-reviewed records that *have* an OA location | **≥ 80%** |

**Extraction/vault gates (vs heuristic baseline: 12 params, 63% temperature):**

| Gate | Target |
|---|---|
| Wheat parameters with a data-point note | **≥ 40 of 85** |
| Single-domain (temperature) share of notes | **< 25%** |
| Domains represented | **≥ 8 of 12** |
| Every data-point note has | ≥1 cited source (document_id + block anchor), ≥2 wikilinks, full facet tags |
| Entity hubs exist for | every crop, domain, parameter-with-data, surfaced method/pest, and cited source |
| Orphan notes | **0** |
| Files written outside `crop_science/` | **0** |

---

## 8. Open / deferred

- **Consensus logic** for conflicting values — default: range + tier-weighted consensus line; refine later.
- **Cross-run source-note dedup** (same DOI across runs) — one source note, many backlinks (content-hash/DOI keyed).
- **Legality of full-text fetch** — respect licenses; paywalled stays metadata-only; only fetch OA/public-domain bytes.
- **Non-wheat crops** — Phase 5.
- **Corpus storage size** — content-addressed blobs dedupe bytes; prune superseded snapshots on demand.
