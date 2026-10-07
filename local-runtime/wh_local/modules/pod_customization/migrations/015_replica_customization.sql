-- 爆款复刻模式：一批一个样图来源，1–200 个目标产品各存一份按款冻结快照。
-- 父批次仍落在 pod_customization_batches（mode='replica'，requested_count=len(targets)），
-- 不重建历史表。名称/类目只存于业务字段快照内，不冗余存第二份可分歧值。
CREATE TABLE IF NOT EXISTS pod_customization_replica_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, owner_user_id, client_request_id),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (source_asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE TABLE IF NOT EXISTS pod_customization_replica_targets (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL CHECK (style_index >= 1),
    asset_id TEXT NOT NULL,
    business_fields_json TEXT NOT NULL,
    listing_fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, style_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_replica_targets_batch
    ON pod_customization_replica_targets (batch_id, style_index);