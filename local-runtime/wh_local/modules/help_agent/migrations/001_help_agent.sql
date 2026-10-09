-- 操作答疑智能体：未命中问题清单
--
-- 设计边界（重要，勿扩展）：
--   本表**刻意不包含 workspace_id / owner_user_id 等任何用户身份字段**。
--   它的唯一用途是统计"用户问了但 FAQ 答不上来的问题"，用于持续补充 FAQ 库。
--   它不是用户行为日志，不允许被扩展成可反查具体用户的追踪表。
--
-- 不含对话历史表：纯 FAQ 检索没有生成内容，当前会话由前端 sessionStorage
-- 持有，刷新即清，无需落盘。
CREATE TABLE IF NOT EXISTS help_agent_missed_questions (
    id          TEXT PRIMARY KEY,
    -- 归一化后的问题文本，作为去重键
    question    TEXT NOT NULL,
    -- 最近一次用户原始问法，便于人工判断该怎么补 FAQ
    raw_sample  TEXT NOT NULL DEFAULT '',
    -- 出现次数，重复提问时累加而非新增行
    hits        INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- question 唯一：保证相同问题归并累加 hits
CREATE UNIQUE INDEX IF NOT EXISTS idx_help_agent_missed_question
    ON help_agent_missed_questions(question);

-- 按热度查询是主要访问模式（hits 降序看高频未命中）
CREATE INDEX IF NOT EXISTS idx_help_agent_missed_hits
    ON help_agent_missed_questions(hits DESC);
