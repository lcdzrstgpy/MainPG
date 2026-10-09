-- combo_kit: 成员商品标记「套装主要商品」（单选）。
-- 用户在上传原图阶段手动点选；驱动标题以主要商品为主名词 + 融合套装主图以它为主角。
ALTER TABLE combo_kit_items ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0;
