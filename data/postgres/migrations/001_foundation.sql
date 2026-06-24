CREATE TABLE IF NOT EXISTS source_documents (
  source_url TEXT PRIMARY KEY,
  source_title TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  document_type TEXT NOT NULL,
  accessed_at TIMESTAMPTZ NOT NULL,
  source_publication_date DATE,
  source_publication_year INTEGER,
  latest_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized_claims (
  claim_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_name TEXT NOT NULL,
  parameter_id TEXT NOT NULL,
  attribute TEXT NOT NULL,
  attribute_subtype TEXT NOT NULL,
  observation_type TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  value_type TEXT NOT NULL,
  raw_value_text TEXT NOT NULL,
  numeric_value DOUBLE PRECISION,
  text_value TEXT,
  range_min DOUBLE PRECISION,
  range_max DOUBLE PRECISION,
  normalized_numeric_value DOUBLE PRECISION,
  normalized_range_min DOUBLE PRECISION,
  normalized_range_max DOUBLE PRECISION,
  unit TEXT,
  normalized_unit TEXT,
  qualifier TEXT,
  location_level TEXT NOT NULL,
  location_name TEXT NOT NULL,
  time_label TEXT NOT NULL,
  source_url TEXT NOT NULL REFERENCES source_documents(source_url),
  source_title TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  document_type TEXT NOT NULL,
  evidence_text TEXT NOT NULL,
  confidence TEXT NOT NULL,
  conflict_status TEXT,
  conflict_reason TEXT,
  status TEXT NOT NULL,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
