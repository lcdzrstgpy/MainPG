# 利润活动模块 GitHub 提交说明书

## 1. 提交范围

负责人：`profit_activity` 模块开发者。提交只应包含：

```text
local-runtime/wh_local/modules/profit_activity/**
local-runtime/tests/profit_activity/**
.gitignore
```

不包含：`local-runtime/wh_local/app/**`、其他 `modules/**`、前端、真实数据库文件、个人 IDE 配置及任何 `.env` 文件。根目录 `.gitignore` 仅用于排除 Python 缓存和 SQLite 运行文件。

## 2. 提交前检查

```powershell
Set-Location E:\MainPG\MainPG
git status --short
git diff --check
$env:PYTHONPATH = ".\local-runtime"
python -m compileall -q local-runtime\wh_local\modules\profit_activity
python -m pytest local-runtime\tests\profit_activity -q
```

要求：

- `git diff --check` 无空白错误；
- 所有测试通过；
- `git status --short` 只列出上面的 `profit_activity` 路径；
- 不出现 `*.db`、`*.db-wal`、`*.db-shm`、`__pycache__`、`.env`。

## 3. 建议的提交命令

在当前 `dev` 分支基础上创建个人功能分支（团队另有命名规范时以团队规则为准）：

```powershell
git switch -c codex/profit-activity-backend
git add .gitignore
git add local-runtime/wh_local/modules/profit_activity
git add local-runtime/tests/profit_activity
git status --short
git commit -m "feat(profit-activity): add local profit activity backend"
git push -u origin codex/profit-activity-backend
```

提交前不要使用 `git add .`，这样可以避免把其他成员的改动或本地数据库一起提交。

## 4. Commit message

```text
feat(profit-activity): add local profit activity backend
```

正文（可选）：

```text
- add US/CO/EC profit calculation and activity eligibility rule
- add versioned settings and stale-calculation protection
- persist records and filter runs with SQLAlchemy on SQLite WAL
- expose FastAPI router contract and module tests
```

## 5. Pull Request 标题与说明（可直接复制）

**标题**

```text
feat: 新增利润活动后端模块
```

**说明**

```markdown
## 背景
实现 `profit_activity` 的本地后端模块，覆盖利润预览、利润记录归档与活动筛选。

## 变更内容
- 新增 US / CO / EC 站点利润计算；US 尾程费按重量梯度计算。
- 新增版本化利润配置，配置更新采用 `expected_revision` 乐观锁。
- 新增计算哈希校验，预览参数或配置变化时拒绝归档。
- 新增负利润二次确认。
- 新增活动筛选：净利润达标 **或** 利润率达标即保留。
- 新增 SQLAlchemy 表模型和 SQLite WAL 配置。
- 新增 FastAPI Router；主应用只需按模块 README 挂载。

## API
- `GET/PUT /api/v1/profit-activity/settings`
- `POST /api/v1/profit-activity/calculate`
- `POST/GET /api/v1/profit-activity/records`
- `POST/GET /api/v1/profit-activity/filter-runs`

## 验证
- [x] 利润计算冒烟测试
- [x] 归档与筛选链路测试
- [x] 配置版本冲突与过期计算拒绝测试
- [x] SQLite WAL 开启检查

## 集成注意事项
本 PR 不修改主应用入口；请由应用装配负责人按 `modules/profit_activity/README.md` 挂载 Router。
```

## 6. Reviewer 检查点

- 公式：US/CO/EC 首程、补贴和尾程费用是否符合业务规则。
- 安全性：归档是否拒绝过期 `calculation_hash`，负利润是否要求确认。
- 数据库：SQLite 是否已启用 WAL；`site_code + skc` 是否唯一。
- 边界：筛选规则是否为“净利润 **或** 利润率”而非“且”。
- 边界清晰：没有越权修改其他模块或主应用入口。
