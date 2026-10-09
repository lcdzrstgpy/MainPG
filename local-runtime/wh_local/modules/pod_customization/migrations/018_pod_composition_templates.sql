-- 构图模板库：一个账号下可保存多份「构图/视角」模板，其中一份「生效」；新建批次使用生效那份。
-- 017 建表时对 (workspace_id, owner_user_id) 建了唯一索引（当时只保留最新一份），这里改为普通索引以支持多份。
DROP INDEX IF EXISTS idx_pod_compositions_owner;

ALTER TABLE pod_customization_compositions ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_compositions ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_pod_compositions_owner
    ON pod_customization_compositions (workspace_id, owner_user_id);

-- 存量数据回填：每个账号把最近更新的一份标为生效。
UPDATE pod_customization_compositions
SET is_active = 1
WHERE rowid IN (
    SELECT c.rowid FROM pod_customization_compositions AS c
    WHERE c.updated_at = (
        SELECT MAX(x.updated_at) FROM pod_customization_compositions AS x
        WHERE x.workspace_id = c.workspace_id AND x.owner_user_id = c.owner_user_id
    )
);
