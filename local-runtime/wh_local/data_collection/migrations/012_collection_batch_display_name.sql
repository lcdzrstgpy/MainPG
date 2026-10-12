-- 采集批次展示名：为三个采集渠道的批次表补充人类可读的 display_name。
-- 批次 ID（run_id / batch_id）保持不变（它是跨表外键），仅新增展示列；
-- 历史数据该列为空，读取侧会按现有字段现算兜底。

ALTER TABLE daily_selection_runs
ADD COLUMN display_name TEXT NOT NULL DEFAULT '';

ALTER TABLE shop_collection_batches
ADD COLUMN display_name TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_onebound_capture_batches
ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
