# 利润活动初始字段空置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清空利润活动页面六个系统预填字段，同时可靠区分未配置门槛与用户已保存门槛，并阻止未配置门槛进入过滤流程。

**Architecture:** 在利润设置模型中增加持久化布尔标记 `activity_threshold_configured`，由数据库迁移区分旧系统 8/20 与其他历史值。前端只在标记为真时回显门槛，并对保存、过滤和生成下载共用同一套门槛解析守卫；后端过滤入口再做一次配置守卫，避免直接 API 调用绕过页面。

**Tech Stack:** React 18、TypeScript 5.6、Vite 5、Python 3、pytest、SQLAlchemy、SQLite、FastAPI/Pydantic

## Global Constraints

- 商品 ID、售价、成本、重量 KG 初始均为空，占位提示与布局保持不变。
- 活动最低实际利润和活动最低利润率仅在用户明确保存后回显。
- “恢复默认设置”清空活动门槛，不再恢复 8 元 / 20%。
- 门槛为空或无效时，不允许保存、产品过滤或生成下载可申报产品；空值不得静默转换成 0。
- 旧值恰好为 8 元和 20% 时按旧系统预设迁移为空；其他旧值保留并视为用户配置。
- 不修改已保存产品、历史过滤结果、布局、标签、占位提示、站点费率或产品资料导入流程。
- 不新增前端测试框架或运行时依赖。

---

### Task 1: 持久化门槛配置状态与旧库迁移

**Files:**
- Create: `local-runtime/tests/test_profit_activity_threshold_configuration.py`
- Create: `local-runtime/wh_local/modules/profit_activity/migrations/005_activity_threshold_configuration.sql`
- Modify: `local-runtime/wh_local/modules/profit_activity/domain/models.py:42-62`
- Modify: `local-runtime/wh_local/modules/profit_activity/infrastructure/orm.py:16-38`
- Modify: `local-runtime/wh_local/modules/profit_activity/infrastructure/database.py:54-99`
- Modify: `local-runtime/wh_local/modules/profit_activity/api/schemas.py:11-31`
- Modify: `local-runtime/wh_local/modules/profit_activity/migrations/001_profit_activity.sql:20-25`
- Modify: `local-runtime/wh_local/db.py:586-632`

**Interfaces:**
- Consumes: existing `ProfitSettings`, `ProfitSettingsRow`, `SettingsPayload`, `create_database`, and shared `init_db` migration registry.
- Produces: `ProfitSettings.activity_threshold_configured: bool`; fresh defaults `activity_min_net_profit=Decimal("0")`, `activity_profit_rate_threshold=Decimal("0")`, configured false; migration `profit_activity:005_activity_threshold_configuration`.

- [ ] **Step 1: Write failing model and migration tests**

Create `local-runtime/tests/test_profit_activity_threshold_configuration.py`:

```python
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from wh_local.modules.profit_activity.domain.models import ProfitSettings
from wh_local.modules.profit_activity.infrastructure.database import create_database
from wh_local.modules.profit_activity.infrastructure.repository import ProfitActivityRepository


MODULE_ROOT = Path(__file__).resolve().parents[1] / "wh_local/modules/profit_activity"
BASE_SCHEMA = MODULE_ROOT / "migrations/001_profit_activity.sql"
THRESHOLD_MIGRATION = MODULE_ROOT / "migrations/005_activity_threshold_configuration.sql"


def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(BASE_SCHEMA.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO profit_activity_settings "
            "(id, workspace_id, activity_min_net_profit, activity_profit_rate_threshold) "
            "VALUES (1, 'legacy-default', 8, 0.20)"
        )
        connection.execute(
            "INSERT INTO profit_activity_settings "
            "(id, workspace_id, activity_min_net_profit, activity_profit_rate_threshold) "
            "VALUES (2, 'legacy-custom', 12, 0.25)"
        )
        connection.commit()
    finally:
        connection.close()


def test_new_profit_settings_start_with_empty_activity_thresholds(tmp_path: Path) -> None:
    settings = ProfitSettings()
    assert settings.activity_min_net_profit == Decimal("0")
    assert settings.activity_profit_rate_threshold == Decimal("0")
    assert settings.activity_threshold_configured is False

    database = create_database(tmp_path / "fresh.sqlite3")
    try:
        stored = ProfitActivityRepository(database.sessions).get_settings().settings
        assert stored.activity_min_net_profit == Decimal("0")
        assert stored.activity_profit_rate_threshold == Decimal("0")
        assert stored.activity_threshold_configured is False
    finally:
        database.dispose()


def test_module_database_migrates_old_default_pair_to_unconfigured(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _legacy_database(path)

    database = create_database(path)
    repository = ProfitActivityRepository(database.sessions)
    try:
        old_default = repository.get_settings("legacy-default").settings
        old_custom = repository.get_settings("legacy-custom").settings
        assert old_default.activity_min_net_profit == Decimal("0")
        assert old_default.activity_profit_rate_threshold == Decimal("0")
        assert old_default.activity_threshold_configured is False
        assert old_custom.activity_min_net_profit == Decimal("12")
        assert old_custom.activity_profit_rate_threshold == Decimal("0.25")
        assert old_custom.activity_threshold_configured is True
    finally:
        database.dispose()


def test_shared_sql_migration_classifies_legacy_thresholds() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE profit_activity_settings ("
            "id INTEGER PRIMARY KEY, workspace_id TEXT NOT NULL UNIQUE, "
            "activity_min_net_profit NUMERIC NOT NULL DEFAULT 8, "
            "activity_profit_rate_threshold NUMERIC NOT NULL DEFAULT 0.20)"
        )
        connection.executemany(
            "INSERT INTO profit_activity_settings VALUES (?, ?, ?, ?)",
            [(1, "default", 8, 0.20), (2, "custom", 12, 0.25)],
        )
        connection.executescript(THRESHOLD_MIGRATION.read_text(encoding="utf-8"))
        rows = connection.execute(
            "SELECT workspace_id, activity_min_net_profit, "
            "activity_profit_rate_threshold, activity_threshold_configured "
            "FROM profit_activity_settings ORDER BY id"
        ).fetchall()
        assert rows == [("default", 0, 0, 0), ("custom", 12, 0.25, 1)]
    finally:
        connection.close()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run from `local-runtime/`:

```bash
python3 -m pytest tests/test_profit_activity_threshold_configuration.py -q
```

Expected: FAIL because `ProfitSettings` still defaults to 8/0.20, has no `activity_threshold_configured`, and migration 005 does not exist.

- [ ] **Step 3: Add the settings field and zero defaults**

In `domain/models.py`, use:

```python
    activity_min_net_profit: Decimal = Decimal("0")
    activity_profit_rate_threshold: Decimal = Decimal("0")
    activity_threshold_configured: bool = False
    rule_version: int = 2
```

In `infrastructure/orm.py`, import `Boolean` and replace the threshold columns with:

```python
    activity_min_net_profit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    activity_profit_rate_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    activity_threshold_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

In `api/schemas.py`, add a backward-compatible full-settings field after the numeric thresholds:

```python
    activity_threshold_configured: bool = True
```

The direct full-settings API represents an explicit user update, so an older full-settings caller that omits the new field remains configured.

In `001_profit_activity.sql`, change only the numeric defaults:

```sql
    activity_min_net_profit NUMERIC(12, 4) NOT NULL DEFAULT 0,
    activity_profit_rate_threshold NUMERIC(10, 6) NOT NULL DEFAULT 0,
```

- [ ] **Step 4: Add shared and module-local migration behavior**

Create `005_activity_threshold_configuration.sql`:

```sql
ALTER TABLE profit_activity_settings
    ADD COLUMN activity_threshold_configured INTEGER NOT NULL DEFAULT 0;

UPDATE profit_activity_settings
SET activity_threshold_configured = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE 1
    END,
    activity_min_net_profit = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE activity_min_net_profit
    END,
    activity_profit_rate_threshold = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE activity_profit_rate_threshold
    END;
```

Register it in `wh_local/db.py` immediately after migration 004:

```python
    profit_activity_threshold_sql = (
        root / "modules" / "profit_activity" / "migrations" / "005_activity_threshold_configuration.sql"
    )
    if profit_activity_threshold_sql.exists():
        migrations.append(
            (
                "profit_activity:005_activity_threshold_configuration",
                "profit_activity",
                profit_activity_threshold_sql.read_text(encoding="utf-8"),
            )
        )
```

In `infrastructure/database.py`, add the legacy column definition:

```python
            "activity_threshold_configured": "BOOLEAN NOT NULL DEFAULT 0",
```

Track and backfill only when that exact column is newly added:

```python
    threshold_state_added = False
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    if table == "profit_activity_settings" and name == "activity_threshold_configured":
                        threshold_state_added = True
        if threshold_state_added:
            connection.exec_driver_sql(
                """
                UPDATE profit_activity_settings
                SET activity_threshold_configured = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE 1
                    END,
                    activity_min_net_profit = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE activity_min_net_profit
                    END,
                    activity_profit_rate_threshold = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE activity_profit_rate_threshold
                    END
                """
            )
```

Do not reclassify databases that already have the flag.

- [ ] **Step 5: Run the focused test and verify GREEN**

```bash
python3 -m pytest tests/test_profit_activity_threshold_configuration.py -q
```

Expected: `3 passed`.

- [ ] **Step 6: Run nearby backend regression tests**

```bash
python3 -m pytest tests/test_profit_activity_dynamic_sites.py tests/test_profit_activity_product_library_editing.py -q
```

Expected: all selected nearby tests pass because Task 1 does not yet introduce the filter guard.

- [ ] **Step 7: Commit Task 1**

```bash
git add local-runtime/tests/test_profit_activity_threshold_configuration.py local-runtime/wh_local/modules/profit_activity/domain/models.py local-runtime/wh_local/modules/profit_activity/infrastructure/orm.py local-runtime/wh_local/modules/profit_activity/infrastructure/database.py local-runtime/wh_local/modules/profit_activity/api/schemas.py local-runtime/wh_local/modules/profit_activity/migrations/001_profit_activity.sql local-runtime/wh_local/modules/profit_activity/migrations/005_activity_threshold_configuration.sql local-runtime/wh_local/db.py
git commit -m "feat: persist profit activity threshold state"
```

### Task 2: 设置保存语义与后端过滤守卫

**Files:**
- Modify: `local-runtime/tests/test_profit_activity_threshold_configuration.py`
- Modify: `local-runtime/tests/test_profit_activity_dynamic_sites.py:73-101`
- Modify: `local-runtime/wh_local/modules/profit_activity/service.py:118-135`
- Modify: `local-runtime/wh_local/modules/profit_activity/service.py:409-470`
- Modify: `local-runtime/wh_local/modules/profit_activity/service.py:610-638`
- Modify: `local-runtime/wh_local/modules/profit_activity/service.py:718-729`

**Interfaces:**
- Consumes: Task 1's `ProfitSettings.activity_threshold_configured` and zero defaults.
- Produces: legacy settings updates that infer configured=true when threshold numbers are explicitly supplied; explicit restore with configured=false; `_require_activity_thresholds(settings: ProfitSettings) -> None`; filter entry points that raise `ProfitValidationError("activity_threshold_not_configured")` before work starts.

- [ ] **Step 1: Add failing save/restore and guard tests**

Append to `test_profit_activity_threshold_configuration.py`:

```python
import pytest

from wh_local.modules.profit_activity.domain.engine import ProfitValidationError
from wh_local.modules.profit_activity.service import ProfitActivityService


def test_legacy_threshold_save_is_persisted_and_restore_clears_it(tmp_path: Path) -> None:
    database = create_database(tmp_path / "settings.sqlite3")
    service = ProfitActivityService(ProfitActivityRepository(database.sessions), database)
    try:
        saved = service.update_legacy_settings({
            "activity_min_net_profit": 12,
            "activity_profit_rate_threshold": 0.25,
        })
        assert saved["activity_min_net_profit"] == Decimal("12")
        assert saved["activity_profit_rate_threshold"] == Decimal("0.25")
        assert saved["activity_threshold_configured"] is True

        restored = service.update_legacy_settings({
            "expected_revision": saved["revision"],
            "activity_min_net_profit": 0,
            "activity_profit_rate_threshold": 0,
            "activity_threshold_configured": False,
        })
        assert restored["activity_min_net_profit"] == Decimal("0")
        assert restored["activity_profit_rate_threshold"] == Decimal("0")
        assert restored["activity_threshold_configured"] is False
    finally:
        service.close()


def test_unconfigured_thresholds_block_filter_entry_points(tmp_path: Path) -> None:
    database = create_database(tmp_path / "filter.sqlite3")
    service = ProfitActivityService(ProfitActivityRepository(database.sessions), database)
    try:
        with pytest.raises(ProfitValidationError, match="activity_threshold_not_configured"):
            service.run_filter("US", None)
        with pytest.raises(ProfitValidationError, match="activity_threshold_not_configured"):
            service.start_activity_filter(b"not-read", "activity.xlsx", "US")
    finally:
        service.close()
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python3 -m pytest tests/test_profit_activity_threshold_configuration.py -q
```

Expected: the save test fails because the bool is converted as a number or remains false; guard tests fail because filtering proceeds without a configured threshold.

- [ ] **Step 3: Preserve boolean type and infer explicit threshold saves**

In `service.py`, replace `_decimal_settings` with:

```python
def _decimal_settings(values: dict[str, Any]) -> dict[str, Any]:
    bool_names = {"activity_threshold_configured"}
    decimal_names = set(ProfitSettings.__dataclass_fields__) - {"save_root", "rule_version", *bool_names}
    return {
        key: (
            _decimal_setting(value)
            if key in decimal_names
            else int(value)
            if key == "rule_version"
            else value is True
            if key in bool_names
            else str(value)
        )
        for key, value in values.items()
    }
```

In `update_legacy_settings`, before constructing `ProfitSettings`, infer an explicit legacy save only when the caller omitted the new flag:

```python
        threshold_fields = {"activity_min_net_profit", "activity_profit_rate_threshold"}
        if "activity_threshold_configured" not in payload and threshold_fields.intersection(payload):
            values["activity_threshold_configured"] = True
```

- [ ] **Step 4: Add one shared backend threshold guard**

Add at module level:

```python
def _require_activity_thresholds(settings: ProfitSettings) -> None:
    if not settings.activity_threshold_configured:
        raise ProfitValidationError("activity_threshold_not_configured")
```

Call it immediately after loading settings in all public filtering paths:

```python
        settings = self.get_settings(actor).settings
        _require_activity_thresholds(settings)
```

Apply this to `filter_activity_template` and `run_filter`. At the start of `start_activity_filter`, call:

```python
        _require_activity_thresholds(self.get_settings(actor).settings)
```

This makes the upload endpoint fail synchronously before it creates a background task.

- [ ] **Step 5: Configure the existing dynamic-site filter test explicitly**

In `test_created_site_is_used_by_calculation_and_product_archive`, before `run_filter`, add:

```python
        service.update_legacy_settings({
            "activity_min_net_profit": 8,
            "activity_profit_rate_threshold": "0.20",
        })
```

- [ ] **Step 6: Run focused and nearby tests and verify GREEN**

```bash
python3 -m pytest tests/test_profit_activity_threshold_configuration.py tests/test_profit_activity_dynamic_sites.py tests/test_profit_activity_product_library_editing.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add local-runtime/tests/test_profit_activity_threshold_configuration.py local-runtime/tests/test_profit_activity_dynamic_sites.py local-runtime/wh_local/modules/profit_activity/service.py
git commit -m "fix: require configured activity thresholds"
```

### Task 3: 前端六字段空置、保存回显与操作守卫

**Files:**
- Create: `web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py`
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx:100-110`
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx:205-223`
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx:515-545`
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx:780-850`
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx:1245-1265`

**Interfaces:**
- Consumes: backend top-level legacy settings field `activity_threshold_configured` from Tasks 1–2.
- Produces: empty initial `ProductForm`; `parseActivityThresholds(settings) -> { minNetProfit: number; minProfitRatePercent: number } | null`; explicit true/false persistence; front-end guards for save/filter/generate; configured-only threshold display.

- [ ] **Step 1: Write the failing front-end regression test**

Create `web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py`:

```python
import unittest
from pathlib import Path


PAGE = (
    Path(__file__).resolve().parents[2]
    / "src/modules/profit_activity/pages/ProfitActivityTestPage.tsx"
)


class ProfitActivityEmptyInitialFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PAGE.read_text(encoding="utf-8")

    def test_single_product_fields_start_empty(self) -> None:
        empty_product = self.source.split(
            "const emptyProduct: ProductForm = {", 1
        )[1].split("\n};", 1)[0]
        for field in ("skc", "selling_price", "cost_price", "weight_kg"):
            with self.subTest(field=field):
                self.assertIn(f'{field}: ""', empty_product)
        self.assertNotIn('selling_price: "19.99"', empty_product)
        self.assertNotIn('cost_price: "5.00"', empty_product)
        self.assertNotIn('weight_kg: "0.35"', empty_product)

    def test_threshold_display_depends_on_explicit_configuration(self) -> None:
        extract = self.source.split(
            "function extractSiteSettings(", 1
        )[1].split("\n}\n\nfunction numericProductPayload", 1)[0]
        self.assertIn("settings.activity_threshold_configured !== true", extract)
        self.assertIn('result[key] = ""', extract)

    def test_save_restore_and_filter_paths_use_threshold_state(self) -> None:
        self.assertIn("function parseActivityThresholds", self.source)
        self.assertIn("activity_threshold_configured: true", self.source)
        self.assertIn("activity_threshold_configured: false", self.source)
        self.assertGreaterEqual(
            self.source.count("if (!activityThresholds)"),
            3,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the front-end test and verify RED**

```bash
python3 -m unittest web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py -v
```

Expected: FAIL because the product initializer contains 19.99/5.00/0.35 and no configured-state display/save/restore/filter guards exist.

- [ ] **Step 3: Clear product defaults and add threshold parsing**

Replace `emptyProduct` with:

```tsx
const emptyProduct: ProductForm = {
  skc: "",
  selling_price: "",
  cost_price: "",
  weight_kg: "",
  note: "",
  source_url: "",
  source_urls: [],
};
```

Remove the duplicate `clearedProduct` constant and change the post-save reset to `setProductForm(emptyProduct)`.

Change the two numeric activity defaults in `DEFAULT_PROFIT_SETTINGS` to 0.

Add this module-level parser:

```tsx
function parseActivityThresholds(values: Record<string, string>) {
  const minNetProfitRaw = (values.activity_min_net_profit ?? "").trim();
  const minProfitRateRaw = (values.activity_profit_rate_threshold ?? "").trim();
  if (!minNetProfitRaw || !minProfitRateRaw) return null;
  const minNetProfit = Number(minNetProfitRaw);
  const minProfitRatePercent = Number(minProfitRateRaw);
  if (!Number.isFinite(minNetProfit) || minNetProfit < 0) return null;
  if (!Number.isFinite(minProfitRatePercent) || minProfitRatePercent < 0 || minProfitRatePercent > 100) return null;
  return { minNetProfit, minProfitRatePercent };
}
```

Inside the component, derive:

```tsx
  const activityThresholds = parseActivityThresholds(siteSettings);
```

- [ ] **Step 4: Save configured thresholds and restore to blank**

In `saveActivityThreshold`, require the parser result and send explicit configuration:

```tsx
    if (!activityThresholds) throw new Error("请填写正确的活动最低实际利润和最低利润率。");
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || ""),
      activity_min_net_profit: activityThresholds.minNetProfit,
      activity_profit_rate_threshold: activityThresholds.minProfitRatePercent / 100,
      activity_threshold_configured: true,
    };
```

In `restoreActivityThreshold`, send zeros and false:

```tsx
      activity_min_net_profit: 0,
      activity_profit_rate_threshold: 0,
      activity_threshold_configured: false,
```

Keep the existing success message and extract the returned settings so the fields clear immediately.

- [ ] **Step 5: Guard both filtering actions**

At the start of `runActivityFilter` and `generateFiltered`, before checking files or completed tasks, add:

```tsx
    if (!activityThresholds) {
      setMessage("请先填写并保存活动最低实际利润和最低利润率。");
      return;
    }
```

The save handler already guards through the same parsed value, so the source contains three shared guard sites: save, product filter, and generate/download.

- [ ] **Step 6: Display thresholds only when configured**

In `extractSiteSettings`, before the threshold loop, add:

```tsx
  if (settings.activity_threshold_configured !== true) {
    result.activity_min_net_profit = "";
    result.activity_profit_rate_threshold = "";
    return result;
  }
```

When configured is true, keep the existing percent conversion so saved values continue to display as yuan and percent.

- [ ] **Step 7: Run focused front-end and backend verification**

```bash
python3 -m unittest web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py -v
python3 -m unittest web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py -v
```

Expected: all front-end integration regression tests pass.

From `local-runtime/`:

```bash
python3 -m pytest tests/test_profit_activity_threshold_configuration.py tests/test_profit_activity_dynamic_sites.py tests/test_profit_activity_product_library_editing.py -q
```

Expected: all selected backend tests pass.

- [ ] **Step 8: Run the production build**

From `web-frontend/`:

```bash
npm run build
```

Expected: `tsc --noEmit` and `vite build` exit 0.

- [ ] **Step 9: Review and commit Task 3**

```bash
git diff --check
git diff -- web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py
git add web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx web-frontend/tests/integration/test_profit_activity_empty_initial_fields.py
git commit -m "fix: clear profit activity initial fields"
```
