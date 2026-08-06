-- 第三板块（货源匹配）中，用户把图搜到的 1688 offer 与 Temu SKC 建立关联。
-- 一个 SKC 可关联保留多个 1688 链接（UNIQUE(workspace_id, skc_id, offer_id)），
-- 关联后入库保留，供出单时按 SKC 反查国内 1688 代发链接。
CREATE TABLE IF NOT EXISTS price_verification_skc_source_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    skc_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL DEFAULT '',
    main_image_url TEXT NOT NULL DEFAULT '',
    price_cny TEXT,
    moq TEXT,
    domestic_freight_cny TEXT,
    source_decision TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'removed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, skc_id, offer_id),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_verification_skc_source_links_skc
    ON price_verification_skc_source_links(workspace_id, skc_id, status);
