-- 当前批次的图搜工作区。它只保存尚未完成入库的临时结果，避免浏览器缓存、
-- 历史批次和产品库记录互相串扰。
CREATE TABLE IF NOT EXISTS price_verification_batch_sourcing_sessions (
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    selected_skc_ids_json TEXT NOT NULL DEFAULT '[]',
    unresolved_skc_ids_json TEXT NOT NULL DEFAULT '[]',
    matched_products_json TEXT NOT NULL DEFAULT '[]',
    preview_json TEXT,
    selected_candidates_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, batch_id),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE
);
