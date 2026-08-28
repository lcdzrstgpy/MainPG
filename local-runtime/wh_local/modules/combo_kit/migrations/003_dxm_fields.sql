-- combo_kit: 新增店小秘导入所需的套装主档字段（用户录入/导出映射用）。
-- 均为可选，导出时用于填充店小秘导入模板的必填列。
ALTER TABLE combo_kit_sets ADD COLUMN declared_price      TEXT NOT NULL DEFAULT '';
ALTER TABLE combo_kit_sets ADD COLUMN length_cm          REAL NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN width_cm           REAL NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN height_cm          REAL NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN weight_g           REAL NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN stock              INTEGER NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN category_name      TEXT NOT NULL DEFAULT '';
ALTER TABLE combo_kit_sets ADD COLUMN suggested_price_usd REAL NOT NULL DEFAULT 0;
ALTER TABLE combo_kit_sets ADD COLUMN id_type            TEXT NOT NULL DEFAULT '';
ALTER TABLE combo_kit_sets ADD COLUMN id_code            TEXT NOT NULL DEFAULT '';
