-- 复刻识图分析与款级冻结计划。
-- 1) 分析快照：A（原素材）与 B（目标产品）的事实、模型/schema 版本、状态与错误码。
-- 2) 款级目标表新增冻结计划列：ReplicaPlan、实际 Prompt 快照与各版本号、生成策略。
-- 历史复刻批次保持旧数据不变，不会被自动重算（新列一律带默认值）。
CREATE TABLE IF NOT EXISTS pod_customization_replica_analyses (
    analysis_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    target_asset_id TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    source_spec_json TEXT NOT NULL DEFAULT '{}',
    target_spec_json TEXT NOT NULL DEFAULT '{}',
    raw_source_json TEXT NOT NULL DEFAULT '{}',
    raw_target_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_asset_id) REFERENCES pod_customization_assets (asset_id),
    FOREIGN KEY (target_asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_replica_analyses_owner
    ON pod_customization_replica_analyses (workspace_id, owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pod_replica_analyses_pair
    ON pod_customization_replica_analyses
       (workspace_id, owner_user_id, source_asset_id, target_asset_id, analysis_version);

ALTER TABLE pod_customization_replica_targets ADD COLUMN analysis_id TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN replica_plan_json TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN prompt_snapshot TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN capability_registry_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN rule_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN template_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN compiler_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_replica_targets ADD COLUMN generation_strategy TEXT NOT NULL DEFAULT '';
