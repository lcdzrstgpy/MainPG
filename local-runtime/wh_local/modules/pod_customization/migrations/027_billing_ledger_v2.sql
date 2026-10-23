-- POD 计费账本 v2（027）：意图账 + 冻结快照 + 结算/明细/对账三张表。
--
-- 为什么必须重建 billing_runs：SQLite 无法放宽 NOT NULL / 扩 CHECK，而意图账要求
-- freeze_id 可空（意图行先落库，此时还没有单号），status 也要容纳新的终态。
-- 与 007 / 008 一样采用「建新表 → 搬迁 → 删旧表 → 改名」，整段在一个事务里完成。
--
-- 历史行没有单价快照 / 单元 ID / settle_deadline，迁移时留空，不反推：
-- 这类行在启动对账里按「无快照」单独统计，不参与 expected 比对。
PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS pod_customization_billing_runs_v3;

CREATE TABLE pod_customization_billing_runs_v3 (
    run_id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL CHECK (action_type IN (
        'batch_initial', 'direct_trial', 'scene_optimization',
        'item_retry', 'style_retry', 'title_retry'
    )),
    target_id TEXT NOT NULL,
    batch_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    -- 意图行先落库：SQLite 的 UNIQUE 允许多个 NULL。
    freeze_id TEXT UNIQUE,
    rule_version INTEGER NOT NULL DEFAULT 0,
    grant_expires_at TEXT NOT NULL DEFAULT '',
    plan_json TEXT NOT NULL,
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    -- intent / authorized / settling / settlement_pending 为非终态；
    -- 其余为终态。resume_claimed / auth_required 是历史状态值，保留以便原样搬迁。
    status TEXT NOT NULL CHECK (status IN (
        'intent', 'authorized', 'resume_claimed', 'settling', 'settlement_pending',
        'auth_required', 'settled', 'abandoned', 'expired_released', 'orphaned', 'anomaly'
    )),
    result_status TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    settled_at TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL DEFAULT '',
    unit_kind TEXT NOT NULL DEFAULT '',
    billing_profile TEXT NOT NULL DEFAULT '',
    link_count INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    -- 金额是 API 原值（JSON number，最多 1 位小数），客户端不换算、不取整。
    frozen_points REAL NOT NULL DEFAULT 0,
    link_prices_json TEXT NOT NULL DEFAULT '[]',
    unit_ids_json TEXT NOT NULL DEFAULT '[]',
    multiplier_percent INTEGER NOT NULL DEFAULT 100,
    settle_deadline TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    freeze_raw_json TEXT NOT NULL DEFAULT '{}',
    release_reason TEXT NOT NULL DEFAULT '',
    zero_billed INTEGER NOT NULL DEFAULT 0,
    batch_deleted_at TEXT NOT NULL DEFAULT ''
);

INSERT INTO pod_customization_billing_runs_v3 (
    run_id, action_key, action_type, target_id, batch_id, workspace_id,
    owner_user_id, freeze_id, rule_version, grant_expires_at, plan_json,
    action_payload_json, status, result_status, error_message, created_at,
    updated_at, settled_at, module, unit_kind, billing_profile
)
SELECT
    run_id, action_key, action_type, target_id, batch_id, workspace_id,
    owner_user_id, freeze_id, rule_version, grant_expires_at, plan_json,
    action_payload_json, status, result_status, error_message, created_at,
    updated_at, settled_at,
    CASE
        WHEN plan_json LIKE '%"billing_profile":"pod_semi_v1"%' THEN 'pod_semi'
        WHEN plan_json LIKE '%"billing_profile":"pod_replica_v1_a"%' THEN 'pod_replica'
        WHEN plan_json LIKE '%"billing_profile":"pod_replica_v1_b"%' THEN 'pod_replica'
        WHEN plan_json LIKE '%"billing_profile":"pod_fission_v1"%' THEN 'pod_fission'
        ELSE 'pod_custom'
    END,
    CASE
        WHEN plan_json LIKE '%"billing_profile":"pod_semi_v1"%' THEN 'group'
        WHEN plan_json LIKE '%"billing_profile":"pod_random_grid_v1"%' THEN 'group'
        ELSE 'style'
    END,
    CASE
        WHEN plan_json LIKE '%"billing_profile":"pod_semi_v1"%' THEN 'pod_semi_v1'
        WHEN plan_json LIKE '%"billing_profile":"pod_replica_v1_a"%' THEN 'pod_replica_v1_a'
        WHEN plan_json LIKE '%"billing_profile":"pod_replica_v1_b"%' THEN 'pod_replica_v1_b'
        WHEN plan_json LIKE '%"billing_profile":"pod_fission_v1"%' THEN 'pod_fission_v1'
        WHEN plan_json LIKE '%"billing_profile":"pod_random_grid_v1"%' THEN 'pod_random_grid_v1'
        WHEN plan_json LIKE '%"billing_profile":"pod_random_v1"%' THEN 'pod_random_v1'
        ELSE ''
    END
FROM pod_customization_billing_runs;

DROP TABLE pod_customization_billing_runs;
ALTER TABLE pod_customization_billing_runs_v3 RENAME TO pod_customization_billing_runs;

CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_owner_status
    ON pod_customization_billing_runs (workspace_id, owner_user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_batch
    ON pod_customization_billing_runs (batch_id, workspace_id, owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_status
    ON pod_customization_billing_runs (status, updated_at);

COMMIT;

PRAGMA foreign_keys = ON;

-- 一次结算 / 收口一行。金额全部来自服务端结算回执；expected_points 是唯一的
-- 本地推导值，只用于告警，不参与账。
CREATE TABLE IF NOT EXISTS pod_customization_billing_settlements (
    settlement_id TEXT PRIMARY KEY,
    freeze_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL CHECK (source IN ('settle', 'close', 'ttl')),
    expected_points REAL NOT NULL DEFAULT 0,
    charged_points REAL NOT NULL DEFAULT 0,
    refunded_points REAL NOT NULL DEFAULT 0,
    released_points REAL,
    retained_points REAL,
    release_reason TEXT NOT NULL DEFAULT '',
    rule_version TEXT NOT NULL DEFAULT '',
    raw_response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    settled_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pod_billing_settlements_created
    ON pod_customization_billing_settlements (created_at);

-- 每个结算单元一行。link_idx 统一 0..N-1；聚合值必须等于明细之和。
CREATE TABLE IF NOT EXISTS pod_customization_billing_settlement_items (
    settlement_id TEXT NOT NULL,
    link_idx INTEGER NOT NULL CHECK (link_idx >= 0),
    unit_id TEXT NOT NULL DEFAULT '',
    feature_status_json TEXT NOT NULL DEFAULT '{}',
    charged_points REAL NOT NULL DEFAULT 0,
    refunded_points REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (settlement_id, link_idx),
    FOREIGN KEY (settlement_id) REFERENCES pod_customization_billing_settlements (settlement_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pod_customization_billing_recon_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    owner_user_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL CHECK (scope IN ('startup', 'scheduled', 'daily')),
    checked_count INTEGER NOT NULL DEFAULT 0,
    repaired_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    loss_points REAL NOT NULL DEFAULT 0,
    daily_difference REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pod_billing_recon_runs_owner
    ON pod_customization_billing_recon_runs (workspace_id, owner_user_id, started_at DESC);
