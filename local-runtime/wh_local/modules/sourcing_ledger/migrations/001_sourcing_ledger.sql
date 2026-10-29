-- 货源与利润台账：把「核价采集的申报价」和「1688 多货源」挂到同一个商品主档上。
-- 商品主档本身沿用 product_processing_drafts（skc = PP-xxxxxx），这里只建附属表。

-- 1) 多货源：一个商品可以挂多条 1688 货源，默认按到手成本最低的一条核算利润。
CREATE TABLE IF NOT EXISTS product_sourcing_sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    -- 商品主档键：新数据为 PP-xxxxxx；历史迁移数据先落 temu:<skc_id>，匹配后再改写。
    product_key TEXT NOT NULL,
    key_kind TEXT NOT NULL DEFAULT 'product_code',
    offer_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    main_image_url TEXT NOT NULL DEFAULT '',
    price_cny TEXT NOT NULL DEFAULT '',
    moq INTEGER NOT NULL DEFAULT 1,
    domestic_freight_cny TEXT NOT NULL DEFAULT '',
    weight_kg TEXT NOT NULL DEFAULT '',
    -- 冗余字段：price + freight / moq，便于直接按到手成本排序与取最低价。
    landed_cost_cny TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_decision TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sourcing_sources_key_url
    ON product_sourcing_sources(workspace_id, product_key, source_url);
CREATE INDEX IF NOT EXISTS idx_sourcing_sources_product
    ON product_sourcing_sources(workspace_id, product_key, status);

-- 2) 申报价：每次采集按 SKU 追加快照，不覆盖历史（默认取最新一次展示）。
CREATE TABLE IF NOT EXISTS product_declared_prices (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'temu',
    market_skc_id TEXT NOT NULL,
    market_skc_code TEXT NOT NULL DEFAULT '',
    -- 匹配到的商品主档货号；未匹配为空。
    skc TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    sku_merchant_code TEXT NOT NULL DEFAULT '',
    sku_attribute_text TEXT NOT NULL DEFAULT '',
    -- 去重键：SKU ID 缺失时依次退到货号 / 规格文本 / 序号，保证同一批同一 SKC 不重复。
    sku_ref TEXT NOT NULL DEFAULT '',
    original_price_cny TEXT NOT NULL DEFAULT '',
    adjusted_price_cny TEXT NOT NULL DEFAULT '',
    product_title TEXT NOT NULL DEFAULT '',
    match_status TEXT NOT NULL DEFAULT 'pending',
    match_method TEXT NOT NULL DEFAULT '',
    match_candidates_json TEXT NOT NULL DEFAULT '[]',
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_declared_prices_sku
    ON product_declared_prices(workspace_id, batch_id, market_skc_id, sku_ref);
CREATE INDEX IF NOT EXISTS idx_declared_prices_skc
    ON product_declared_prices(workspace_id, skc, captured_at);
CREATE INDEX IF NOT EXISTS idx_declared_prices_pending
    ON product_declared_prices(workspace_id, match_status);

-- 3) 商品主档 ↔ 市场商品绑定：人工确认一次后永久生效，后续采集自动命中。
CREATE TABLE IF NOT EXISTS product_market_links (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'temu',
    site TEXT NOT NULL DEFAULT '',
    market_skc_id TEXT NOT NULL,
    market_skc_code TEXT NOT NULL DEFAULT '',
    title_snapshot TEXT NOT NULL DEFAULT '',
    match_method TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc, market, site)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_links_market
    ON product_market_links(workspace_id, market, market_skc_id, site);

-- 4) 上架标题：标题兜底匹配用（草稿 title 是 1688 中文原标题，真正上架的是处理后的英文标题）。
CREATE TABLE IF NOT EXISTS product_listing_states (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    listed_title TEXT NOT NULL DEFAULT '',
    title_key TEXT NOT NULL DEFAULT '',
    listed_at TEXT NOT NULL DEFAULT '',
    listing_status TEXT NOT NULL DEFAULT 'unlisted',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc)
);

CREATE INDEX IF NOT EXISTS idx_listing_states_title
    ON product_listing_states(workspace_id, title_key);

-- 5) 一次性任务标记（历史货源迁移是否已执行）。
CREATE TABLE IF NOT EXISTS sourcing_ledger_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
