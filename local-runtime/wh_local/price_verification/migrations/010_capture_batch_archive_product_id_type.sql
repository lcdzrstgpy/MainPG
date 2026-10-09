-- 当前核价批次写入产品库时采用的业务标识；历史批次保持 SKC。
ALTER TABLE price_verification_quote_capture_batches
    ADD COLUMN archive_product_id_type TEXT NOT NULL DEFAULT 'SKC';
