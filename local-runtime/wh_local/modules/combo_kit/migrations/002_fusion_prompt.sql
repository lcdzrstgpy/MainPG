-- combo_kit: 新增「融合套装主图」用户自定义提示词（可选）。
-- 用户在主体解析阶段填写的融合提示词；为空时使用内置融合模板。
ALTER TABLE combo_kit_sets ADD COLUMN fusion_prompt TEXT NOT NULL DEFAULT '';
