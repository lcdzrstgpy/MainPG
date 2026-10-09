-- 批次人工筛选的"待审重组列表"。
-- 第一板块（批次报价审核）勾选确认后，把选中的 SKC 重组成待审条目；
-- 第二板块（最终确认）逐条保留/删除，保留时写入草稿池并可按 SKC 指定图搜相似品数量。
CREATE TABLE IF NOT EXISTS price_verification_batch_selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    skc_id TEXT NOT NULL,
    quote_keys_json TEXT NOT NULL DEFAULT '[]',
    product_title TEXT NOT NULL DEFAULT '',
    main_image_url TEXT NOT NULL DEFAULT '',
    official_link_url TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    source_confidence TEXT NOT NULL DEFAULT '',
    authenticity_status TEXT NOT NULL DEFAULT '',
    sku_prices_json TEXT NOT NULL DEFAULT '[]',
    original_min TEXT,
    original_max TEXT,
    adjusted_min TEXT,
    adjusted_max TEXT,
    max_candidates INTEGER NOT NULL DEFAULT 10
        CHECK (max_candidates BETWEEN 1 AND 100),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'retained', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, batch_id, skc_id),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_verification_batch_selections_batch
    ON price_verification_batch_selections(workspace_id, batch_id, status);
