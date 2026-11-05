-- 订单利润：插件采集的 Temu 订单行 + 详情页金额，按父订单/子订单/SKU 合并。
-- command_id 作为幂等键，同一采集命令重复 ingest 不会重复写行。

CREATE TABLE IF NOT EXISTS order_profit_batches (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    command_id INTEGER NOT NULL,
    capture_mode TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ingested',
    order_count INTEGER NOT NULL DEFAULT 0,
    sub_order_count INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, command_id)
);

CREATE INDEX IF NOT EXISTS idx_order_profit_batches_workspace
    ON order_profit_batches(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS order_profit_items (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL DEFAULT '',
    command_id INTEGER NOT NULL DEFAULT 0,
    parent_order_sn TEXT NOT NULL DEFAULT '',
    sub_order_sn TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    skc_id TEXT NOT NULL DEFAULT '',
    skc_code TEXT NOT NULL DEFAULT '',
    ext_code TEXT NOT NULL DEFAULT '',
    goods_name TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1,
    declared_price REAL,
    declared_currency TEXT NOT NULL DEFAULT '',
    gmv REAL,
    gmv_cny REAL,
    gmv_currency TEXT NOT NULL DEFAULT '',
    sales_payback REAL,
    sales_reversal REAL,
    freight_payback REAL,
    freight_reversal REAL,
    estimated_income REAL,
    order_payment REAL,
    second_payment REAL,
    tail_freight REAL,
    tail_currency TEXT NOT NULL DEFAULT '',
    tail_freight_cny REAL,
    tracking_no TEXT NOT NULL DEFAULT '',
    order_status TEXT NOT NULL DEFAULT '',
    order_status_code TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    order_time TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    mall_id TEXT NOT NULL DEFAULT '',
    unit_cost_cny REAL,
    purchase_cost_cny REAL,
    weight_kg REAL,
    cost_source TEXT NOT NULL DEFAULT '',
    match_status TEXT NOT NULL DEFAULT '',
    commission_cny REAL,
    head_freight_cny REAL,
    profit_estimate_cny REAL,
    profit_rate REAL,
    source TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, parent_order_sn, sub_order_sn, sku_id)
);

CREATE INDEX IF NOT EXISTS idx_order_profit_items_month
    ON order_profit_items(workspace_id, order_time, parent_order_sn);
CREATE INDEX IF NOT EXISTS idx_order_profit_items_sku
    ON order_profit_items(workspace_id, sku_id);
CREATE INDEX IF NOT EXISTS idx_order_profit_items_store
    ON order_profit_items(workspace_id, mall_id, shop_name);

CREATE TABLE IF NOT EXISTS order_profit_settings (
    workspace_id TEXT PRIMARY KEY,
    usd_cny_rate REAL NOT NULL DEFAULT 7.2,
    commission_rate REAL NOT NULL DEFAULT 0.05,
    head_unit_price_cny REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- 运营工具侧边栏的数据结构。链接变动/售后/申诉/流量暂时没有采集源，
-- 先建表并提供查询接口，后续接入 Temu 接口后直接写入即可。
CREATE TABLE IF NOT EXISTS operation_link_changes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    product_key TEXT NOT NULL DEFAULT '',
    product_title TEXT NOT NULL DEFAULT '',
    old_url TEXT NOT NULL DEFAULT '',
    new_url TEXT NOT NULL DEFAULT '',
    change_type TEXT NOT NULL DEFAULT '',
    detected_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_link_changes_workspace
    ON operation_link_changes(workspace_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS operation_after_sales (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    parent_order_sn TEXT NOT NULL DEFAULT '',
    sub_order_sn TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    amount REAL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    occurred_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_after_sales_workspace
    ON operation_after_sales(workspace_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS operation_appeals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    parent_order_sn TEXT NOT NULL DEFAULT '',
    sub_order_sn TEXT NOT NULL DEFAULT '',
    appeal_type TEXT NOT NULL DEFAULT '',
    deadline TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_appeals_workspace
    ON operation_appeals(workspace_id, deadline);

CREATE TABLE IF NOT EXISTS operation_traffic_weekly (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    week_start TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    visitors INTEGER NOT NULL DEFAULT 0,
    page_views INTEGER NOT NULL DEFAULT 0,
    orders INTEGER NOT NULL DEFAULT 0,
    gmv REAL NOT NULL DEFAULT 0,
    conversion_rate REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, week_start, site)
);

-- 对账中心/财务明细：推广服务费、罚款、结算等流水。
CREATE TABLE IF NOT EXISTS finance_entries (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    entry_time TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    note TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, entry_time, entry_type, amount, note)
);
CREATE INDEX IF NOT EXISTS idx_finance_entries_month
    ON finance_entries(workspace_id, entry_time);

-- 店铺首页指标（待发货、在售商品、销量、退款/取消待处理、及时率）。
CREATE TABLE IF NOT EXISTS store_metrics (
    workspace_id TEXT PRIMARY KEY,
    shop_name TEXT NOT NULL DEFAULT '',
    pending_shipment INTEGER NOT NULL DEFAULT 0,
    active_products INTEGER NOT NULL DEFAULT 0,
    sales_today INTEGER NOT NULL DEFAULT 0,
    sales_7d INTEGER NOT NULL DEFAULT 0,
    sales_30d INTEGER NOT NULL DEFAULT 0,
    refund_pending INTEGER NOT NULL DEFAULT 0,
    cancel_pending INTEGER NOT NULL DEFAULT 0,
    ship_timeliness REAL,
    delivery_timeliness REAL,
    captured_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- 结算数据（账户资金 → 结算数据）：按 PO 单记录销售回款/冲回、运费回款/冲回。
-- status 区分「待处理款项」(pending) 与「已到账款项」(settled)。
CREATE TABLE IF NOT EXISTS order_settlements (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    parent_order_sn TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    currency TEXT NOT NULL DEFAULT 'CNY',
    sales_payback REAL,
    sales_reversal REAL,
    freight_payback REAL,
    freight_reversal REAL,
    declared_price REAL,
    quantity INTEGER NOT NULL DEFAULT 0,
    goods_name TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, parent_order_sn, status, currency)
);
CREATE INDEX IF NOT EXISTS idx_order_settlements_workspace
    ON order_settlements(workspace_id, status, parent_order_sn);

-- 经营分析 → 商品流量/店铺流量：汇总指标按「范围 + 周期 + 统计日期」落一份快照。
CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'product',
    period TEXT NOT NULL DEFAULT '',
    stat_date TEXT NOT NULL DEFAULT '',
    exposure INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    visitors INTEGER NOT NULL DEFAULT 0,
    paid_orders INTEGER NOT NULL DEFAULT 0,
    paid_items INTEGER NOT NULL DEFAULT 0,
    click_rate REAL,
    click_order_rate REAL,
    exposure_order_rate REAL,
    metrics TEXT NOT NULL DEFAULT '{}',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, scope, period, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_traffic_snapshots_workspace
    ON traffic_snapshots(workspace_id, scope, captured_at DESC);

-- 经营分析 → 销量分析：数据概览 + 售罄健康，按「区域 + 统计日期」落快照。
CREATE TABLE IF NOT EXISTS sales_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    stat_date TEXT NOT NULL DEFAULT '',
    metrics TEXT NOT NULL DEFAULT '{}',
    health TEXT NOT NULL DEFAULT '{}',
    shop_name TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, region, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_sales_snapshots_workspace
    ON sales_snapshots(workspace_id, captured_at DESC);

-- 店铺绑定：工作台里维护的店铺清单，采集到的数据按当前绑定店铺归类。
CREATE TABLE IF NOT EXISTS profit_shops (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'temu',
    note TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    last_capture_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);
CREATE INDEX IF NOT EXISTS idx_profit_shops_workspace
    ON profit_shops(workspace_id, is_active DESC, created_at);


