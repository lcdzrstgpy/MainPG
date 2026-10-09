-- Additive v2 storage. Existing flat item batches intentionally remain untouched.
CREATE TABLE IF NOT EXISTS pod_customization_style_grid_batches (
    batch_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pod_customization_style_grid_results (
    result_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    style_index INTEGER NOT NULL,
    variant_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    pattern_asset_id TEXT NOT NULL DEFAULT '',
    composite_asset_id TEXT NOT NULL DEFAULT '',
    pattern_fingerprint TEXT NOT NULL DEFAULT '',
    scene_optimized INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, style_index, variant_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_style_grid_results_batch
    ON pod_customization_style_grid_results (batch_id, style_index, variant_index);
