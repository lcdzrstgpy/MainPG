-- 申报价 SKU 选择：同一 SKC 有多个 SKU 且调整后申报价不同时，允许用户指定用哪个 SKU 核算；
-- 未指定时仍默认取最低价。SKU 以 sku_ref 标识（与采集去重键一致），跨采集批次稳定。
CREATE TABLE IF NOT EXISTS product_declared_price_selections (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    sku_ref TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc)
);

-- 用户在抽屉里删除的 SKU：不再参与最低价/利润计算，后续采集回流也不会重新出现。
CREATE TABLE IF NOT EXISTS product_declared_price_exclusions (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    sku_ref TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc, sku_ref)
);
