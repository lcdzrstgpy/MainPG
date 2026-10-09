-- Additive POD customization schema. Legacy ai_service POD tables are intentionally
-- untouched so upgrades preserve every historical conversation and generated file.
CREATE TABLE IF NOT EXISTS pod_customization_assets (
    asset_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_assets_owner
    ON pod_customization_assets (workspace_id, owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pod_customization_templates (
    template_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('system', 'personal')),
    asset_id TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    calibration_status TEXT NOT NULL,
    calibration_json TEXT NOT NULL DEFAULT 'null',
    error_message TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_templates_owner
    ON pod_customization_templates (workspace_id, owner_user_id, deleted_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS pod_customization_template_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    calibration_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (template_id, version),
    FOREIGN KEY (template_id) REFERENCES pod_customization_templates (template_id),
    FOREIGN KEY (asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_template_snapshots_owner
    ON pod_customization_template_snapshots (workspace_id, owner_user_id, template_id, version DESC);

CREATE TABLE IF NOT EXISTS pod_customization_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_snapshot_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    requested_count INTEGER NOT NULL CHECK (requested_count IN (20, 40, 100)),
    processed_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    initial_call_count INTEGER NOT NULL,
    refill_call_count INTEGER NOT NULL DEFAULT 0,
    max_refill_calls INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_snapshot TEXT NOT NULL,
    business_fields_json TEXT NOT NULL,
    creative_prompt TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (template_id) REFERENCES pod_customization_templates (template_id),
    FOREIGN KEY (template_snapshot_id) REFERENCES pod_customization_template_snapshots (snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_batches_owner
    ON pod_customization_batches (workspace_id, owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pod_customization_batches_status
    ON pod_customization_batches (status, updated_at);

CREATE TABLE IF NOT EXISTS pod_customization_batch_items (
    item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    pattern_asset_id TEXT NOT NULL DEFAULT '',
    composite_asset_id TEXT NOT NULL DEFAULT '',
    pattern_fingerprint TEXT NOT NULL DEFAULT '',
    scene_optimized INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, item_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_batch_items_owner
    ON pod_customization_batch_items (batch_id, workspace_id, owner_user_id, item_index);

CREATE TABLE IF NOT EXISTS pod_customization_generation_calls (
    call_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    call_kind TEXT NOT NULL,
    call_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    prompt_snapshot TEXT NOT NULL,
    grid_asset_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    UNIQUE (batch_id, call_kind, call_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_generation_calls_batch
    ON pod_customization_generation_calls (batch_id, call_kind, call_index);

CREATE TABLE IF NOT EXISTS pod_customization_pattern_candidates (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    grid_cell INTEGER NOT NULL,
    status TEXT NOT NULL,
    rejection_reason TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    pattern_asset_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (call_id) REFERENCES pod_customization_generation_calls (call_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_pattern_candidates_batch
    ON pod_customization_pattern_candidates (batch_id, status, created_at);
