-- combo_kit: 套装生成选型（bundle=套装组合 / multiview=单品多视角）。
ALTER TABLE combo_kit_sets ADD COLUMN generation_mode TEXT NOT NULL DEFAULT 'bundle';
