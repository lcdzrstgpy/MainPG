-- 产品库记录来源标记：核价及货源板块「已关联 1688 货源」自动入库的记录标记为 price_verification，
-- 产品库可按来源筛选、展示来源徽标；手工导入/核算的记录保持默认 manual。
ALTER TABLE profit_activity_records ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual';

CREATE INDEX IF NOT EXISTS idx_profit_activity_records_source_type
    ON profit_activity_records (workspace_id, source_type);
