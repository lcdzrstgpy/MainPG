-- 订单归属记录：同一条订单可能被两个店铺各采过一次。
-- order_profit_items 的唯一键会把这些行合并成一条，所以这里单独记「哪家店采到过这条订单」，
-- 用于给「同一账号不同店铺重复订单」打标记（只标记、不删数据）。

CREATE TABLE IF NOT EXISTS order_shop_claims (
    workspace_id    TEXT NOT NULL,
    parent_order_sn TEXT NOT NULL DEFAULT '',
    sub_order_sn    TEXT NOT NULL DEFAULT '',
    sku_id          TEXT NOT NULL DEFAULT '',
    shop_name       TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, parent_order_sn, sub_order_sn, sku_id, shop_name)
);

CREATE INDEX IF NOT EXISTS idx_order_shop_claims_shop
    ON order_shop_claims(workspace_id, shop_name);
