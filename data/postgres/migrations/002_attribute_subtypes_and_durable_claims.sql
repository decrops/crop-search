ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS attribute_subtype TEXT;

ALTER TABLE normalized_claims
  ADD COLUMN IF NOT EXISTS parameter_id TEXT;

UPDATE normalized_claims
SET attribute_subtype = attribute
WHERE attribute_subtype IS NULL;

UPDATE normalized_claims
SET parameter_id = 'unmapped.' || attribute_subtype
WHERE parameter_id IS NULL;

ALTER TABLE normalized_claims
  ALTER COLUMN attribute_subtype SET NOT NULL;

ALTER TABLE normalized_claims
  ALTER COLUMN parameter_id SET NOT NULL;

CREATE TABLE IF NOT EXISTS durable_claims (
  durable_claim_id TEXT PRIMARY KEY,
  source_claim_id TEXT NOT NULL REFERENCES normalized_claims(claim_id),
  run_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_name TEXT NOT NULL,
  parameter_id TEXT NOT NULL,
  attribute TEXT NOT NULL,
  attribute_subtype TEXT NOT NULL,
  promotion_decision TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  value_json JSONB NOT NULL,
  location_scope_json JSONB NOT NULL,
  time_scope_json JSONB NOT NULL,
  provenance_json JSONB NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL,
  promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_normalized_claims_attribute_subtype
  ON normalized_claims (attribute_subtype);

CREATE INDEX IF NOT EXISTS idx_normalized_claims_parameter_id
  ON normalized_claims (parameter_id);

CREATE INDEX IF NOT EXISTS idx_normalized_claims_scope
  ON normalized_claims (location_level, location_name, time_label);

CREATE INDEX IF NOT EXISTS idx_durable_claims_attribute_subtype
  ON durable_claims (attribute_subtype);

CREATE INDEX IF NOT EXISTS idx_durable_claims_parameter_id
  ON durable_claims (parameter_id);
