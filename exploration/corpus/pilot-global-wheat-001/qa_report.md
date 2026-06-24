# Raw Corpus QA Report — pilot-global-wheat-001

| Metric | Value |
|---|---|
| Captures | 825 |
| Unique documents | 341 |
| Documents with text | 280 |
| Capture redundancy collapsed | 0.587 |
| Duplicate text ratio (Opus input) | 0.0 |
| Metadata-only | 61 (0.179) |
| Background (Wikipedia) | 30 (0.088) |
| Short-text docs | 23 |
| Docs with tables | 79 |
| Raw fetch failures | 300 |
| Params with documents | 85 |
| Params missing documents | 0 |

## Gates

- [x] duplicate_text_ratio_lt_0.10
- [ ] metadata_only_share_lt_0.15
- [x] background_share_lt_0.15
- [x] has_tables

**Gates passed: False**

## High-value retry queue (parameters with zero documents)

