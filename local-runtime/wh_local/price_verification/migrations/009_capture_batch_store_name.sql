-- 核价批次的可选店铺归属。历史批次与未填写店铺的批次保持为空。
ALTER TABLE price_verification_quote_capture_batches
    ADD COLUMN store_name TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_price_verification_capture_batches_workspace_store
    ON price_verification_quote_capture_batches (workspace_id, store_name);
