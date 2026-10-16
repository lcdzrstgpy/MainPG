-- Fission-specific analysis, batch, and variant snapshots.
-- The public POD batch remains the execution anchor; these tables only carry
-- fission inputs and frozen plans so older POD modes keep their schema.
CREATE TABLE IF NOT EXISTS pod_customization_fission_analyses (
    analysis_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    target_asset_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    source_spec_json TEXT NOT NULL DEFAULT '{}',
    target_spec_json TEXT NOT NULL DEFAULT '{}',
    user_summary_json TEXT NOT NULL DEFAULT '{}',
    raw_response_json TEXT NOT NULL DEFAULT '{}',
    candidate_payload_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '',
    capability_registry_version INTEGER NOT NULL DEFAULT 0,
    rule_registry_version INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_asset_id) REFERENCES pod_customization_assets (asset_id),
    FOREIGN KEY (target_asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_fission_analyses_owner
    ON pod_customization_fission_analyses (workspace_id, owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pod_fission_analyses_pair
    ON pod_customization_fission_analyses
       (workspace_id, owner_user_id, source_asset_id, target_asset_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pod_customization_fission_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 10),
    listing_fields_json TEXT NOT NULL DEFAULT '{}',
    composition_json TEXT NOT NULL DEFAULT '{}',
    capability_registry_version INTEGER NOT NULL DEFAULT 0,
    rule_registry_version INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT NOT NULL DEFAULT '',
    compiler_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'partial', 'completed', 'failed', 'cancelled', 'paused')),
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, owner_user_id, client_request_id),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (analysis_id) REFERENCES pod_customization_fission_analyses (analysis_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_fission_batches_owner
    ON pod_customization_fission_batches (workspace_id, owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pod_fission_batches_status
    ON pod_customization_fission_batches (status, updated_at);

CREATE TABLE IF NOT EXISTS pod_customization_fission_variants (
    variant_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    style_index INTEGER NOT NULL CHECK (style_index >= 1),
    variant_plan_json TEXT NOT NULL DEFAULT '{}',
    combination_fingerprint TEXT NOT NULL,
    prompt_snapshot TEXT NOT NULL DEFAULT '',
    image_status TEXT NOT NULL DEFAULT 'queued',
    title_status TEXT NOT NULL DEFAULT 'queued',
    spec_card_status TEXT NOT NULL DEFAULT 'queued',
    panel_asset_ids_json TEXT NOT NULL DEFAULT '{}',
    public_urls_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, style_index),
    UNIQUE (batch_id, combination_fingerprint),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_fission_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_fission_variants_owner
    ON pod_customization_fission_variants (workspace_id, owner_user_id, batch_id, style_index);
