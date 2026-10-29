-- 裂变改为「AI 一次出 N 套方案 + 主图与伴生三格」。
-- 方案正文随分析记录冻结；批次记录生图策略与派生模板版本。
ALTER TABLE pod_customization_fission_analyses ADD COLUMN plans_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE pod_customization_fission_analyses ADD COLUMN plan_prompt_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_fission_batches ADD COLUMN generation_strategy TEXT NOT NULL DEFAULT 'grid';
ALTER TABLE pod_customization_fission_batches ADD COLUMN panel_prompt_version TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pod_fission_batches_strategy
    ON pod_customization_fission_batches (generation_strategy, status);
