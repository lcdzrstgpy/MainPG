-- 商品店铺（可选）：货源与利润抽屉里手动填写/选择，入产品库时一并带上。
CREATE TABLE IF NOT EXISTS product_item_store_overrides (
    workspace_id TEXT NOT NULL,
    skc TEXT NOT NULL,
    store_name TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, skc)
);
