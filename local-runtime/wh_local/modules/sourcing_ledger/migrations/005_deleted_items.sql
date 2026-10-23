-- 用户主动删除的台账商品墓碑：启动/详情兜底会按采集数据补货源，需跳过这些商品。
CREATE TABLE IF NOT EXISTS product_sourcing_deleted (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc)
);
