-- combo_kit 独立业务表（与 product_processing / pod_customization 完全隔离）
-- 每个套装 = 单个独立 SKU；子商品仅为素材，不生成子 SKU。

-- 1) 套装主表：SKU 信息、状态、属性
CREATE TABLE IF NOT EXISTS combo_kit_sets (
    set_id                 TEXT PRIMARY KEY,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    name                   TEXT NOT NULL DEFAULT '',      -- 套装名称（用户录入/AI）
    sku                    TEXT NOT NULL DEFAULT '',      -- 套装 SKU（唯一，可编辑）
    sku_display            TEXT NOT NULL DEFAULT '',      -- 最终 SKU 全称（用户可编辑）
    description            TEXT NOT NULL DEFAULT '',      -- 套装详情描述
    bullets_json           TEXT NOT NULL DEFAULT '[]',    -- 五点特性描述
    category_path          TEXT NOT NULL DEFAULT '',
    category_id            TEXT NOT NULL DEFAULT '',
    attributes_json        TEXT NOT NULL DEFAULT '{}',    -- 套装属性
    sku_specs_json         TEXT NOT NULL DEFAULT '[]',    -- 各子商品规格（用户录入）
    status                 TEXT NOT NULL DEFAULT 'draft', -- draft/subject_ready/text_ready/images_ready/preview_pending/completed/failed
    stage                  TEXT NOT NULL DEFAULT '',      -- 当前业务步骤
    text_result_json       TEXT NOT NULL DEFAULT '{}',    -- 文本产出（标题/描述/五点）
    image_results_json     TEXT NOT NULL DEFAULT '[]',    -- 6 张成品图 URL（主图+轮播2+细节+详情+场景）
    error_message          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    started_at             TEXT,
    finished_at            TEXT
);

-- 2) 套装子商品素材表：原图、主体词、蒙版数据、解析结果、规格
CREATE TABLE IF NOT EXISTS combo_kit_items (
    item_id                TEXT PRIMARY KEY,
    set_id                 TEXT NOT NULL,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    item_index             INTEGER NOT NULL DEFAULT 0,    -- 展示顺序
    original_asset_id      TEXT NOT NULL DEFAULT '',      -- 原图（本地受管）
    original_path          TEXT NOT NULL DEFAULT '',      -- 原图落盘路径
    original_url           TEXT NOT NULL DEFAULT '',      -- 对外 URL
    subject_keywords       TEXT NOT NULL DEFAULT '',      -- 用户填写的主体词
    mask_json              TEXT NOT NULL DEFAULT '{}',    -- 人工蒙版（归一化多边形/座标）
    mask_inverted          INTEGER NOT NULL DEFAULT 0,    -- 反选
    mask_regex_serial      INTEGER NOT NULL DEFAULT 0,    -- 蒙版版本（重绘递增）
    subject_parsed_json    TEXT NOT NULL DEFAULT '{}',    -- AI 主体解析结果
    spec_text              TEXT NOT NULL DEFAULT '',      -- 该子商品规格
    width                  INTEGER NOT NULL DEFAULT 0,
    height                 INTEGER NOT NULL DEFAULT 0,
    error_message          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

-- 3) 套装 Prompt 配置表：两套基础模板 + 每张生成图的独立辅助 Prompt
CREATE TABLE IF NOT EXISTS combo_kit_prompts (
    prompt_id              TEXT PRIMARY KEY,
    set_id                 TEXT NOT NULL,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    base_prompt_a          TEXT NOT NULL DEFAULT '',      -- 基础通用模板 A
    base_prompt_b          TEXT NOT NULL DEFAULT '',      -- 基础通用模板 B
    image_prompts_json     TEXT NOT NULL DEFAULT '{}',    -- {"main":..,"carousel_1":..,...} 每图独立辅助
    updated_at             TEXT NOT NULL
);

-- 4) 套装 AI 任务表：文本任务 / 生图任务独立状态
CREATE TABLE IF NOT EXISTS combo_kit_tasks (
    task_id                TEXT PRIMARY KEY,
    set_id                 TEXT NOT NULL,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    task_type              TEXT NOT NULL,                 -- text / image
    status                 TEXT NOT NULL DEFAULT 'queued',-- queued/running/completed/failed
    prompt_snapshot_json   TEXT NOT NULL DEFAULT '{}',    -- 任务发起时的 Prompt 快照
    result_json            TEXT NOT NULL DEFAULT '{}',    -- 文本产出或 6 图结果
    attempt_count          INTEGER NOT NULL DEFAULT 0,
    error_kind             TEXT NOT NULL DEFAULT '',
    error_message          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    started_at             TEXT,
    finished_at            TEXT,
    UNIQUE(set_id, task_type)
);

-- 5) 套装积分扣费记录表（文本/生图隔离、可溯源）
CREATE TABLE IF NOT EXISTS combo_kit_billing (
    billing_id             TEXT PRIMARY KEY,
    set_id                 TEXT NOT NULL,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    billing_type           TEXT NOT NULL,                 -- text / image
    freeze_id              TEXT NOT NULL DEFAULT '',
    rule_version           INTEGER NOT NULL DEFAULT 0,
    points                 INTEGER NOT NULL DEFAULT 0,    -- 20(文本) / 100(生图)
    status                 TEXT NOT NULL DEFAULT 'frozen',-- frozen/settled/released
    result_status          TEXT NOT NULL DEFAULT '',      -- success / no_return / partial
    settled_at             TEXT,
    error_message          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

-- 6) 套装预检记录表
CREATE TABLE IF NOT EXISTS combo_kit_previews (
    preview_id             TEXT PRIMARY KEY,
    set_id                 TEXT NOT NULL,
    workspace_id           TEXT NOT NULL,
    owner_user_id          TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'pending', -- pending/passed/rejected
    payload_json           TEXT NOT NULL DEFAULT '{}',    -- SKU/标题/图文素材/各图Prompt/扣费记录快照
    reject_reason          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE(set_id)
);

CREATE INDEX IF NOT EXISTS idx_combo_kit_sets_workspace ON combo_kit_sets(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_combo_kit_sets_status    ON combo_kit_sets(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_combo_kit_items_set      ON combo_kit_items(set_id, item_index);
CREATE INDEX IF NOT EXISTS idx_combo_kit_prompts_set    ON combo_kit_prompts(set_id);
CREATE INDEX IF NOT EXISTS idx_combo_kit_tasks_set      ON combo_kit_tasks(set_id, task_type);
CREATE INDEX IF NOT EXISTS idx_combo_kit_billing_set    ON combo_kit_billing(set_id, billing_type, created_at);
CREATE INDEX IF NOT EXISTS idx_combo_kit_previews_set   ON combo_kit_previews(set_id);
