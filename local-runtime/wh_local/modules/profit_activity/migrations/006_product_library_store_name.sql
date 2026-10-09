-- 产品库店铺归属：可选字段，历史记录与未选择店铺的入库记录保持为空。
ALTER TABLE profit_activity_records
    ADD COLUMN store_name TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_profit_activity_records_workspace_store
    ON profit_activity_records (workspace_id, store_name);
