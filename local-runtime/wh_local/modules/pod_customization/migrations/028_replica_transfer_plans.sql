-- 复刻图案迁移方案：每个 A/B 对一次豆包调用只产出一条「主图提示词」。
-- 同一 (workspace, owner, A, B, 提示词版本, 生图模型, 参考图顺序) 唯一，
-- 重复提交直接复用，不再调用豆包；参考图顺序进入唯一键，避免不同顺序串用同一方案。
CREATE TABLE IF NOT EXISTS pod_customization_replica_transfer_plans (
    plan_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    target_asset_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    reference_order TEXT NOT NULL,
    main_prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, owner_user_id, source_asset_id, target_asset_id, prompt_version, model, reference_order),
    FOREIGN KEY (source_asset_id) REFERENCES pod_customization_assets (asset_id),
    FOREIGN KEY (target_asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_replica_transfer_pair
    ON pod_customization_replica_transfer_plans
       (workspace_id, owner_user_id, source_asset_id, target_asset_id, created_at DESC);
