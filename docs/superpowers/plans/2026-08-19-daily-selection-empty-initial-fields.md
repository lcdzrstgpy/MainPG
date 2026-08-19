# 每日选品采集表单初始空置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每日选品的关键词采集与参考图采集在首次打开时显示空白字段，同时保留用户主动选择模板后的完整回填行为。

**Architecture:** 保持 `DailySelectionPage` 现有的分散 React state，不引入新的状态层或依赖。初始化状态使用空字符串和已确认的安全开关值；`chooseDirection` 负责恢复进入模板时原先可见的模板采集值；提交入口显式拒绝未完成的必填选择，避免空白界面暗中使用回退值。

**Tech Stack:** React 18、TypeScript 5.6、Vite 5、Python `unittest` 源码回归测试

## Global Constraints

- 仅修改每日选品 OneBound API 采集表单的关键词采集与参考图采集两个模式。
- 初始采集平台、站点、选品范围、数量、关键词、参考图 URL、价格、起订量及 SKU 筛选字段为空。
- 自动排除高风险候选初始关闭；采集并行数初始为 1 线程。
- 用户主动选择采集方向后，恢复原先可见的模板采集值。
- 不调整布局、提示文字、模板持久化结构或其他页面。

---

### Task 1: 空白初始状态与模板回填

**Files:**
- Create: `web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py`
- Modify: `web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx:243-260`
- Modify: `web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx:468-476`
- Modify: `web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx:598-633`
- Modify: `web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx:963-1020`

**Interfaces:**
- Consumes: existing `Direction`, `CollectionPlatform`, `TargetSite`, `SelectionScope`, `numberOrUndefined`, and `chooseDirection` behavior.
- Produces: empty initial form state; `chooseDirection(direction: Direction): void` that restores `1688`、方向站点、`divergent`、方向数量/关键词/价格、起订量 2、风险排除开启及 6 线程； submit-time required-field validation.

- [ ] **Step 1: Write the failing regression test**

Create `web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py`:

```python
import unittest
from pathlib import Path


PAGE = (
    Path(__file__).resolve().parents[2]
    / "src/modules/daily_selection/pages/DailySelectionPage.tsx"
)


class DailySelectionEmptyInitialFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PAGE.read_text(encoding="utf-8")

    def test_initial_collection_fields_are_empty(self) -> None:
        expected_initializers = [
            'useState<CollectionPlatform | "">("")',
            'useState<SelectionScope | "">("")',
            'useState<TargetSite | "">("")',
            'const [keywords, setKeywords] = useState("")',
            'const [minPrice, setMinPrice] = useState("")',
            'const [maxPrice, setMaxPrice] = useState("")',
            'const [minMoq, setMinMoq] = useState("")',
            'const [targetCount, setTargetCount] = useState("")',
            'const [excludeRisks, setExcludeRisks] = useState(false)',
            'const [maxParallelCollect, setMaxParallelCollect] = useState(1)',
        ]
        for initializer in expected_initializers:
            with self.subTest(initializer=initializer):
                self.assertIn(initializer, self.source)
        self.assertGreaterEqual(
            self.source.count('<option value="" disabled>请选择</option>'),
            3,
        )

    def test_selecting_a_direction_restores_template_collection_values(self) -> None:
        choose_direction = self.source.split(
            "function chooseDirection(direction: Direction) {", 1
        )[1].split("\n  }", 1)[0]
        expected_updates = [
            'setPlatform("1688")',
            'setScope("divergent")',
            'setMinMoq("2")',
            'setExcludeRisks(true)',
            'setMaxParallelCollect(6)',
            'setKeywords(direction.keywords.join("，"))',
            'setSite(direction.site ?? "US")',
            'setMinPrice(String(direction.price[0]))',
            'setMaxPrice(String(direction.price[1]))',
            'setTargetCount(String(direction.target))',
        ]
        for update in expected_updates:
            with self.subTest(update=update):
                self.assertIn(update, choose_direction)

    def test_switching_collection_mode_does_not_refill_fields(self) -> None:
        mode_tabs = self.source.split(
            '<div className="mode-tabs"', 1
        )[1].split(
            '<div className={`collection-primary-fields', 1
        )[0]
        self.assertIn('onClick={() => setMode(value)}', mode_tabs)
        for setter in (
            "setPlatform(",
            "setSite(",
            "setScope(",
            "setTargetCount(",
            "setKeywords(",
            "setReferenceImageUrl(",
        ):
            with self.subTest(setter=setter):
                self.assertNotIn(setter, mode_tabs)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py -v
```

Expected: FAIL in `test_initial_collection_fields_are_empty` because the component still initializes from `selectedDirection`, and FAIL in `test_selecting_a_direction_restores_template_collection_values` because the general collection defaults are not yet restored inside `chooseDirection`.

- [ ] **Step 3: Implement the minimal empty initial state**

In `DailySelectionPage.tsx`, replace only the collection-form initializers with:

```tsx
  const [platform, setPlatform] = useState<CollectionPlatform | "">("");
  const [keywords, setKeywords] = useState("");
  const [referenceImageUrl, setReferenceImageUrl] = useState("");
  const [scope, setScope] = useState<SelectionScope | "">("");
  const [site, setSite] = useState<TargetSite | "">("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [minMoq, setMinMoq] = useState("");
  const [minSkuCount, setMinSkuCount] = useState("");
  const [maxSkuCount, setMaxSkuCount] = useState("");
  const [minSkuPrice, setMinSkuPrice] = useState("");
  const [maxSkuPrice, setMaxSkuPrice] = useState("");
  const [minSkuStock, setMinSkuStock] = useState("");
  const [maxSkuStock, setMaxSkuStock] = useState("");
  const [targetCount, setTargetCount] = useState("");
  const [excludeRisks, setExcludeRisks] = useState(false);
  const [maxParallelCollect, setMaxParallelCollect] = useState(1);
```

Extend `chooseDirection` so deliberate template selection restores the values that were visible before this change:

```tsx
  function chooseDirection(direction: Direction) {
    setSelectedDirectionId(direction.id);
    setPlatform("1688");
    setKeywords(direction.keywords.join("，"));
    setScope("divergent");
    setSite(direction.site ?? "US");
    setMinPrice(String(direction.price[0]));
    setMaxPrice(String(direction.price[1]));
    setMinMoq("2");
    setTargetCount(String(direction.target));
    setExcludeRisks(true);
    setMaxParallelCollect(6);
  }
```

Add the disabled placeholder option at the top of each collection-form select and preserve the existing options:

```tsx
<option value="" disabled>请选择</option>
```

Cast each select change to its empty-capable state type, for example:

```tsx
onChange={(event) => setPlatform(event.target.value as CollectionPlatform | "")}
```

- [ ] **Step 4: Add required-field guards and remove hidden target fallback**

At the start of `submitCollection`, after clearing messages, add:

```tsx
    if (!platform || !site || !scope) {
      setError("请选择采集平台、站点和选品范围");
      return;
    }
    const parsedTargetCount = numberOrUndefined(targetCount);
    if (parsedTargetCount === undefined || !Number.isInteger(parsedTargetCount) || parsedTargetCount < 1) {
      setError("请填写正确的采集数量");
      return;
    }
```

At the start of `buildCriteria`, narrow the empty-capable select states and parse the target count again because this function owns the request object:

```tsx
    if (!platform || !site || !scope) {
      throw new Error("采集平台、站点和选品范围不能为空");
    }
    const parsedTargetCount = numberOrUndefined(targetCount);
    if (parsedTargetCount === undefined || !Number.isInteger(parsedTargetCount) || parsedTargetCount < 1) {
      throw new Error("采集数量必须是正整数");
    }
```

Use `parsedTargetCount` directly in the criteria object:

```tsx
      target_count: parsedTargetCount,
```

Prevent the empty platform from rendering a channel-specific note before validation:

```tsx
{platform && platform !== "1688" && (
```

Keep the existing helper copy unchanged, and treat the empty platform like the existing 1688 branch until the user makes a selection:

```tsx
<span>{!platform || platform === "1688"
  ? "1688 每批最多调用 200 次 API，并在预算内尽量拉取全部候选的详情（SKU/发源地/属性），失败或下架商品除外。"
  : "淘宝渠道当前仅展示前端交互，不会发送采集请求或产生 API 费用。"}</span>
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run:

```bash
python3 -m unittest web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py -v
```

Expected: all three tests PASS.

- [ ] **Step 6: Run TypeScript and production build verification**

Run:

```bash
npm run build
```

from `web-frontend/`.

Expected: `tsc --noEmit` and `vite build` both exit 0 with no TypeScript errors.

- [ ] **Step 7: Review the final diff**

Run:

```bash
git diff --check
git diff -- web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py
```

Expected: no whitespace errors; diff is limited to the empty initial state, template restoration, validation, placeholder options, and the focused regression test.

- [ ] **Step 8: Commit the implementation**

```bash
git add web-frontend/src/modules/daily_selection/pages/DailySelectionPage.tsx web-frontend/tests/integration/test_daily_selection_empty_initial_fields.py
git commit -m "fix: clear daily selection initial form values"
```
