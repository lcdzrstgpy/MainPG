"""团队服务的数据库结构。

所有业务数据都以 ``store_code`` 为归属维度：
同一家店铺无论被几个账号上报，最后都落在同一行 ``team_store_daily`` 上，
天然满足「同一店铺只保留一个」。
"""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
-- 账号画像：员工标识在首次建店时填写，之后用于辨识和报表归属。
CREATE TABLE IF NOT EXISTS team_account_profiles (
    account_id    TEXT PRIMARY KEY,
    username      TEXT NOT NULL DEFAULT '',
    staff_no      TEXT NOT NULL DEFAULT '',
    staff_no_norm TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_account_staff_no
    ON team_account_profiles(staff_no_norm) WHERE staff_no_norm <> '';

-- 店铺注册表：店铺码由服务器按「日期 + 当天顺序」发放，店名全局唯一。
CREATE TABLE IF NOT EXISTS team_stores (
    store_code      TEXT PRIMARY KEY,
    store_name      TEXT NOT NULL,
    store_name_norm TEXT NOT NULL,
    owner_account_id TEXT NOT NULL,
    platform        TEXT NOT NULL DEFAULT 'temu',
    mall_id         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_store_name
    ON team_stores(store_name_norm);
CREATE INDEX IF NOT EXISTS idx_team_store_owner
    ON team_stores(owner_account_id, status);

-- 账号 ↔ 店铺 绑定：owner 是归属账号，helper 是协助录入的账号。
CREATE TABLE IF NOT EXISTS team_store_bindings (
    store_code TEXT NOT NULL,
    account_id TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'owner',
    bound_at   TEXT NOT NULL,
    PRIMARY KEY (store_code, account_id)
);
CREATE INDEX IF NOT EXISTS idx_team_binding_account
    ON team_store_bindings(account_id);

-- 店铺日汇总：每个店铺每天只保留一行，源数据更新时间更新的才覆盖。
CREATE TABLE IF NOT EXISTS team_store_daily (
    store_code          TEXT NOT NULL,
    stat_date           TEXT NOT NULL,
    source_account_id   TEXT NOT NULL,
    source_updated_at   TEXT NOT NULL,
    order_count         INTEGER NOT NULL DEFAULT 0,
    sub_order_count     INTEGER NOT NULL DEFAULT 0,
    gmv_cny             REAL NOT NULL DEFAULT 0,
    refund_cny          REAL NOT NULL DEFAULT 0,
    profit_cny          REAL,
    settlement_payback_cny REAL NOT NULL DEFAULT 0,
    finance_expense_cny    REAL NOT NULL DEFAULT 0,
    exposure            INTEGER NOT NULL DEFAULT 0,
    clicks              INTEGER NOT NULL DEFAULT 0,
    visitors            INTEGER NOT NULL DEFAULT 0,
    paid_items          INTEGER NOT NULL DEFAULT 0,
    sales_today         INTEGER NOT NULL DEFAULT 0,
    pending_shipment    INTEGER NOT NULL DEFAULT 0,
    active_products     INTEGER NOT NULL DEFAULT 0,
    pushed_at           TEXT NOT NULL,
    PRIMARY KEY (store_code, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_team_daily_date
    ON team_store_daily(stat_date, store_code);

-- 店铺明细：订单 / 财务 / 结算 / 流量 / 销量 / 店铺指标。
-- 客户端每次提交都是整店全量，服务端按店铺整体替换，避免残留旧行。
CREATE TABLE IF NOT EXISTS team_store_records (
    store_code        TEXT NOT NULL,
    kind              TEXT NOT NULL,
    record_key        TEXT NOT NULL,
    stat_date         TEXT NOT NULL DEFAULT '',
    payload           TEXT NOT NULL DEFAULT '{}',
    source_account_id TEXT NOT NULL DEFAULT '',
    pushed_at         TEXT NOT NULL,
    PRIMARY KEY (store_code, kind, record_key)
);
CREATE INDEX IF NOT EXISTS idx_team_records_kind_date
    ON team_store_records(kind, stat_date, store_code);

-- 采集批次：工作台每次采集入库的批次记录，按账号保存，供团队看板分类展示。
CREATE TABLE IF NOT EXISTS team_capture_batches (
    account_id      TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    capture_mode    TEXT NOT NULL DEFAULT '',
    order_count     INTEGER NOT NULL DEFAULT 0,
    sub_order_count INTEGER NOT NULL DEFAULT 0,
    item_count      INTEGER NOT NULL DEFAULT 0,
    captured_at     TEXT NOT NULL DEFAULT '',
    pushed_at       TEXT NOT NULL,
    PRIMARY KEY (account_id, batch_id)
);
CREATE INDEX IF NOT EXISTS idx_team_batches_account
    ON team_capture_batches(account_id, captured_at);

-- 每周六的提交单：同一账号同一周只建一张，重复提交视为补交。
CREATE TABLE IF NOT EXISTS team_submissions (
    submission_id TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    week_start    TEXT NOT NULL,
    submitted_at  TEXT NOT NULL,
    store_count   INTEGER NOT NULL DEFAULT 0,
    day_rows      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'accepted',
    UNIQUE (account_id, week_start)
);

-- 查重标记：只标记、不删数据。
CREATE TABLE IF NOT EXISTS team_dedupe_flags (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,
    account_id       TEXT NOT NULL,
    store_code       TEXT NOT NULL,
    other_store_code TEXT NOT NULL DEFAULT '',
    data_key         TEXT NOT NULL DEFAULT '',
    detail           TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    UNIQUE (kind, account_id, store_code, data_key)
);

-- 账号关联：A 申请关联 B，B 同意后 A 才能看到 B 名下店铺的数据。
-- 每个账号最多主动关联 3 个；被关联方不限制，且能看到有多少人关联了自己。
CREATE TABLE IF NOT EXISTS team_account_links (
    linker_account_id TEXT NOT NULL,
    target_account_id TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (linker_account_id, target_account_id)
);
CREATE INDEX IF NOT EXISTS idx_team_link_target
    ON team_account_links(target_account_id, status);

-- 组长组：谁能看谁的账号。
CREATE TABLE IF NOT EXISTS team_groups (
    group_id          TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    leader_account_id TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
    group_id          TEXT NOT NULL,
    account_id        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    granted_at        TEXT NOT NULL,
    PRIMARY KEY (group_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_team_member_account
    ON team_members(account_id, status);

-- 店铺码发号器的每日游标，只增不减。
CREATE TABLE IF NOT EXISTS store_code_sequences (
    biz_date TEXT PRIMARY KEY,
    next_seq INTEGER NOT NULL DEFAULT 1
);
"""


def initialize(conn: sqlite3.Connection) -> None:
    """建表（幂等），服务启动时调用一次。"""
    conn.executescript(SCHEMA_SQL)
    # 老库没有 status 列（只有已生效的关联），补上并把历史数据视为已同意。
    columns = {row[1] for row in conn.execute("PRAGMA table_info(team_account_links)")}
    if "status" not in columns:
        conn.execute(
            "ALTER TABLE team_account_links "
            "ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'"
        )
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE team_account_links ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    conn.commit()
