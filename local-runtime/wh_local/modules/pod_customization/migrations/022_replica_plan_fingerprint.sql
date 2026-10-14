-- 复刻计划输入指纹：同一份分析/事实覆盖/展示偏好/构图模板/版本生成同一份 ReplicaPlan。
-- 已复刻过的输入直接复用冻结计划，不再重复调用豆包生成构图参数（既省时省钱，
-- 也保证「同一输入必得同一计划」的可复现约束不被模型随机性破坏）。
ALTER TABLE pod_customization_replica_targets ADD COLUMN plan_fingerprint TEXT NOT NULL DEFAULT '';

-- 该表无 workspace/owner 列（经 batch_id 归属），按指纹建单列索引即可命中。
CREATE INDEX IF NOT EXISTS idx_pod_replica_targets_plan_fingerprint
    ON pod_customization_replica_targets (plan_fingerprint);
