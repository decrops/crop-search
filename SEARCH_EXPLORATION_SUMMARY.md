# Search exploration summary for the new repo

## Goal
The main product is a provenance-aware crop knowledge system, not just a database. The web-search step should be treated as part of the data pipeline, with early experimentation used to understand real input heterogeneity before locking the final schema.

## Core conclusions

1. Search must be built into the pipeline early.
   - Web research is part of data acquisition, not an optional later enhancement.
   - The real shape of extracted evidence affects schema, validation, loading, and provenance.
   - Deferring search creates false confidence: the system looks complete while the data supply path remains unproven.

2. A database alone is not enough.
   - A relational database can store researched data, but it should not be designed in isolation from real extraction behavior.
   - Search produces heterogeneous, noisy, incomplete, and inconsistent inputs that influence how the final model must work.

3. Raw capture is useful for discovery.
   - A temporary document store or raw JSON capture layer is valuable for observing real search output heterogeneity.
   - This helps identify common fields, missing metadata, source inconsistency, parsing failures, and normalization needs.

4. Raw capture must remain staging, not the system of record.
   - Raw documents are useful for exploration and pilot learning.
   - The final source of truth should be a normalized, provenance-backed, validated structure.
   - Long-term production storage should move into the main relational schema after the real extraction contract is understood.

5. Separate experimentation is a good idea.
   - A sandbox or separate repo is useful for early search experiments.
   - This keeps the main repo focused on production architecture and avoids mixing discovery work with core implementation.
   - The experiment should feed findings back into the main project after stabilization.

## Recommended architecture

### Exploration stage
- Capture raw search results in a temporary document store or raw files.
- Record source metadata, snippets, candidate fields, and failures.
- Analyze heterogeneity and identify stable patterns.

### Normalization stage
- Convert raw search artifacts into a common extraction contract.
- Enforce required provenance fields, source URLs, access dates, gaps, claims, and confidence.
- Validate units, ranges, and citation presence.

### Production stage
- Load normalized records into PostgreSQL.
- Preserve provenance, conflicts, and source links.
- Keep county/regional augmentation separate from baseline claims.

## Why not wait until after the database is built

- The database schema should reflect real extracted data, not guessed structure.
- Search output drives provenance requirements, confidence representation, gap handling, and conflict modeling.
- Building the database first risks a schema that stores messy data poorly and requires redesign later.

## Suggested strategy

1. Build a small experimental search capture area.
2. Gather representative web results and inspect heterogeneity.
3. Derive the extraction contract from observed data.
4. Build the validator and normalization pipeline against that contract.
5. Load only normalized, validated data into the main production schema.
6. Scale from pilot to broader coverage only after source-backed ingestion is reliable.

## Working principle

Use raw capture to understand the wild input. Use normalized, provenance-backed storage to build the actual product. Keep experimentation separate, but feed it into the production path.
