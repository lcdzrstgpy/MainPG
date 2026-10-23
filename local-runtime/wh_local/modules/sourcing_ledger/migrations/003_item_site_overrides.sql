-- 商品站点手动覆盖：默认站点来自插件采集的申报价，允许用户按商品改站点。
CREATE TABLE IF NOT EXISTS product_item_site_overrides (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    site_label TEXT NOT NULL DEFAULT '',
    site_code TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc)
);
