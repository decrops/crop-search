# Raw Corpus QA Report — eu-wheat-protection-001

| Metric | Value |
|---|---|
| Captures | 96 |
| Unique documents | 28 |
| Documents with text | 25 |
| Capture redundancy collapsed | 0.479 |
| Duplicate text ratio (Opus input) | 0.0 |
| Metadata-only | 3 (0.107) |
| Background (Wikipedia) | 0 (0.0) |
| Short-text docs | 4 |
| Docs with tables | 1 |
| Raw fetch failures | 2 |
| Params with documents | 36 |
| Params missing documents | 63 |

## Gates

- [x] duplicate_text_ratio_lt_0.10
- [x] metadata_only_share_lt_0.15
- [x] background_share_lt_0.15
- [x] has_tables

**Gates passed: True**

## High-value retry queue (parameters with zero documents)

- canopy.biomass_accumulation
- canopy.canopy_cover
- canopy.harvest_index
- canopy.light_extinction_coefficient
- canopy.radiation_use_efficiency
- crop_protection.insect_action_threshold
- crop_protection.nematode_pressure
- harvest.drydown_rate
- harvest.physiological_maturity
- harvest.preharvest_sprouting_risk
- management.residue_cover_response
- management.rotation_recommendation
- management.tillage_response
- morphology.grains_per_spike
- morphology.plant_height
- morphology.spike_density
- morphology.thousand_kernel_weight
- nutrients.critical_tissue_nitrogen
- nutrients.nitrogen_timing
- nutrients.nitrogen_use_efficiency
- nutrients.potassium_requirement
- phenology.emergence_duration
- phenology.grain_fill_duration
- phenology.leaf_appearance_rate
- phenology.photoperiod_sensitivity
- phenology.tillering_duration
- photosynthesis.photosynthetic_rate
- photosynthesis.stomatal_conductance
- photosynthesis.transpiration_efficiency
- photosynthesis.vapor_pressure_deficit_threshold
- planting.emergence_rate
- planting.row_spacing
- planting.target_plant_density
- quality.falling_number
- quality.gluten_strength
- quality.grain_protein
- quality.test_weight
- root.maximum_rooting_depth
- root.root_length_density
- root.root_shoot_ratio
- soil.compaction_sensitivity
- soil.drainage_requirement
- soil.soil_water_holding_capacity
- soil.texture_suitability
- soil.water_table_depth
- stress.cold_tolerance
- stress.salinity_tolerance
- temperature.base_temperature
- temperature.germination_temperature
- temperature.grain_fill_temperature
- temperature.heat_stress_threshold
- temperature.maximum_growth_temperature
- temperature.minimum_growth_temperature
- temperature.photosynthesis_optimum_temperature
- temperature.reproductive_heat_threshold
- temperature.soil_emergence_temperature
- temperature.survival_temperature
- thermal_time.anthesis_gdd
- thermal_time.emergence_gdd
- thermal_time.maturity_gdd
- thermal_time.vernalization_units
- water.water_productivity
- water.waterlogging_sensitivity
