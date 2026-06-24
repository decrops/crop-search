# Raw Corpus QA Report — pilot-global-wheat-002

| Metric | Value |
|---|---|
| Captures | 368 |
| Unique documents | 80 |
| Documents with text | 61 |
| Capture redundancy collapsed | 0.72 |
| Duplicate text ratio (Opus input) | 0.0 |
| Metadata-only | 19 (0.237) |
| Background (Wikipedia) | 3 (0.037) |
| Short-text docs | 7 |
| Docs with tables | 7 |
| Raw fetch failures | 11 |
| Params with documents | 75 |
| Params missing documents | 10 |

## Gates

- [x] duplicate_text_ratio_lt_0.10
- [ ] metadata_only_share_lt_0.15
- [x] background_share_lt_0.15
- [x] has_tables

**Gates passed: False**

## High-value retry queue (parameters with zero documents)

- canopy.light_extinction_coefficient
- harvest.drydown_rate
- morphology.thousand_kernel_weight
- phenology.photoperiod_sensitivity
- phenology.tillering_duration
- photosynthesis.photosynthetic_rate
- photosynthesis.transpiration_efficiency
- photosynthesis.vapor_pressure_deficit_threshold
- temperature.grain_fill_temperature
- thermal_time.vernalization_units
