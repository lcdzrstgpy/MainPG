# POD Spec Card Display Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conditional centimetre/inch output-unit selector so POD spec-card images and Dianxiaomi exports follow the selected unit while stored batch dimensions remain centimetres.

**Architecture:** Store centimetre cells as the single source of truth and add `display_unit` to the frozen spec-card configuration. A small backend conversion module derives image cells and export values at their consumption boundaries; the frontend only selects and transports the unit.

**Tech Stack:** React 18, TypeScript, Node test runner, Python 3.12, Pydantic v2, Pillow, pytest, openpyxl.

## Global Constraints

- `display_unit` accepts exactly `cm` or `in`; missing values default to `cm`.
- User input and batch-management detail data remain centimetres.
- Inch output uses `cm / 2.54`, at most two decimal places, with trailing zeroes removed.
- The image header shows `Length (cm/in)`, `Width (cm/in)`, and `Height (cm/in)`.
- Dianxiaomi cells contain converted numeric values without unit suffixes.
- The unit control is visible only when printing is enabled and preserves its value while hidden.

---

### Task 1: Backend Unit Contract and Conversion Boundary

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/spec_card_units.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_spec_card.py`

**Interfaces:**
- Produces: `SPEC_CARD_DISPLAY_UNIT_CM`, `SPEC_CARD_DISPLAY_UNIT_IN`, `display_spec_card_cells(cells, unit)`, and `display_dimension_value(value, unit)`.
- Produces: `SpecCardConfig.display_unit` and `SpecCardRequestBase.display_unit`, both defaulting to `cm`.

- [ ] **Step 1: Write failing tests for defaulting, validation, and conversion**

```python
def test_spec_card_display_unit_defaults_to_centimetres_and_rejects_unknown() -> None:
    assert SpecCardConfig.from_mapping({"cells": [["SKU", "Length", "Width", "Height"]]}).display_unit == "cm"
    with pytest.raises(ValueError, match="规格卡单位"):
        SpecCardConfig.from_mapping({"display_unit": "feet"})


def test_display_spec_card_cells_converts_inches_without_mutating_centimetres() -> None:
    source = (("SKU", "Length", "Width", "Height"), ("A", "30", "25.4", "10"))
    assert display_spec_card_cells(source, "in") == (
        ("SKU", "Length (in)", "Width (in)", "Height (in)"),
        ("A", "11.81", "10", "3.94"),
    )
    assert source[1][1:] == ("30", "25.4", "10")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_spec_card.py -k 'display_unit or display_spec_card_cells' -q`

Expected: FAIL because `display_unit` and `spec_card_units` do not exist.

- [ ] **Step 3: Implement the shared converter and contract field**

```python
SPEC_CARD_DISPLAY_UNIT_CM = "cm"
SPEC_CARD_DISPLAY_UNIT_IN = "in"
SPEC_CARD_DISPLAY_UNITS = (SPEC_CARD_DISPLAY_UNIT_CM, SPEC_CARD_DISPLAY_UNIT_IN)

def display_dimension_value(value: object, unit: str) -> str:
    text = str(value).strip()
    if unit == SPEC_CARD_DISPLAY_UNIT_CM:
        return text
    converted = (Decimal(text) / Decimal("2.54")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(converted, "f").rstrip("0").rstrip(".")
```

Add `display_unit: str = "cm"` to both Pydantic models, validate against the two constants, include it in `from_mapping()` and `config_mapping()`, and derive headers plus rows in `display_spec_card_cells()` without mutating `cells`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_spec_card.py -k 'display_unit or display_spec_card_cells' -q`

Expected: PASS.

### Task 2: Image Preview, Generation, and Reprint Conversion

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_spec_card_service.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_worker_unit.py`

**Interfaces:**
- Consumes: `display_spec_card_cells(config.cells, config.display_unit)` from Task 1.
- Produces: identical derived cells for preview, normal generation, and reprint.

- [ ] **Step 1: Write failing tests that capture renderer request cells**

```python
payload = {
    "enabled": True,
    "style": "light",
    "corner": "bottom-right",
    "display_unit": "in",
    "cells": [["SKU", "Length", "Width", "Height"], ["A", "25.4", "50.8", "76.2"]],
}
assert captured_request.cells == (
    ("SKU", "Length (in)", "Width (in)", "Height (in)"),
    ("A", "10", "20", "30"),
)
```

Cover preview, `_spec_card_publish_media`, and `_reprint_style_spec_card`.

- [ ] **Step 2: Run the three focused tests and confirm RED**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_spec_card_service.py wh_local/modules/pod_customization/tests/test_worker_unit.py -k 'display_unit or inches' -q`

Expected: FAIL because raw centimetre cells reach the renderer.

- [ ] **Step 3: Convert only at renderer boundaries**

```python
cells=display_spec_card_cells(config.cells, config.display_unit)
```

Use this expression when constructing each `SpecCardRequest`; leave stored `config.cells` untouched.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_spec_card_service.py wh_local/modules/pod_customization/tests/test_worker_unit.py -k 'display_unit or inches' -q`

Expected: PASS.

### Task 3: Dianxiaomi Export Conversion

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/export.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_dianxiaomi_export.py`

**Interfaces:**
- Consumes: `display_dimension_value(value, display_unit)` from Task 1.
- Produces: `_sku_dimensions()` values in the configured export unit while leaving `listing_fields.spec_card.cells` unchanged.

- [ ] **Step 1: Write the failing workbook test**

```python
spec_card={
    "display_unit": "in",
    "cells": [
        ["SKU", "Length", "Width", "Height"],
        ["CT-BLACK", "25.4", "50.8", "76.2"],
    ],
}
assert row[11:14] == (10, 20, 30)
assert stored_listing["spec_card"]["cells"][1][1:4] == ["25.4", "50.8", "76.2"]
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_dianxiaomi_export.py -k 'display_unit' -q`

Expected: FAIL with centimetre values in columns 11–13.

- [ ] **Step 3: Apply unit conversion in `_sku_dimensions()`**

Read `display_unit` from `listing_fields.spec_card`, default to `cm`, transform the matched row through `display_dimension_value`, then pass each result through `_cell_number` so workbook cells remain numeric.

- [ ] **Step 4: Run export tests and confirm GREEN**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_dianxiaomi_export.py -q`

Expected: PASS.

### Task 4: Frontend Unit Control and Snapshot Propagation

**Files:**
- Modify: `web-frontend/src/modules/pod_customization/types/index.ts`
- Modify: `web-frontend/src/modules/pod_customization/data/podCustomizationModel.ts`
- Modify: `web-frontend/src/modules/pod_customization/data/podCustomizationDraft.ts`
- Modify: `web-frontend/src/modules/pod_customization/components/SpecCardAppearanceControls.tsx`
- Modify: `web-frontend/src/modules/pod_customization/components/SpecCardDrawer.tsx`
- Modify: `web-frontend/src/modules/pod_customization/components/SpecCardPreview.tsx`
- Modify: `web-frontend/src/modules/pod_customization/styles/podCustomization.css`
- Test: `web-frontend/src/modules/pod_customization/pages/PodSpecCardEntry.test.ts`
- Test: `web-frontend/src/modules/pod_customization/data/podCustomizationModel.test.ts`
- Test: `web-frontend/src/modules/pod_customization/data/podCustomizationDraft.test.ts`

**Interfaces:**
- Produces: `SpecCardDisplayUnit = "cm" | "in"` and required `display_unit` on config, preview, and reprint request types.
- Consumes: backend `display_unit` contract from Task 1.

- [ ] **Step 1: Write failing model, draft, and UI tests**

Assert that defaults use `cm`, clones and API snapshots retain `in`, legacy draft values without the field normalize to `cm`, and source structure contains:

```tsx
{enabled && (
  <div className="pod-spec-card-field">
    <span className="pod-spec-card-field-label">印图单位<em>*</em></span>
    <div role="radiogroup" aria-label="印图单位">...</div>
  </div>
)}
```

- [ ] **Step 2: Run frontend focused tests and confirm RED**

Run: `cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/pages/PodSpecCardEntry.test.ts src/modules/pod_customization/data/podCustomizationModel.test.ts src/modules/pod_customization/data/podCustomizationDraft.test.ts`

Expected: FAIL because `display_unit` and the conditional control are absent.

- [ ] **Step 3: Implement frontend state and payload propagation**

Add `display_unit: "cm"` to defaults, normalize missing legacy values to `cm`, preserve it in clones, and add drawer state:

```tsx
const [displayUnit, setDisplayUnit] = useState<SpecCardDisplayUnit>(config.display_unit);
```

Pass it through `currentConfig`, preview, save, and reprint. Render two radio cards (`厘米/cm`, `英寸/in`) only when `enabled` is true and do not reset `displayUnit` when false.

- [ ] **Step 4: Run focused frontend tests and confirm GREEN**

Run: `cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/pages/PodSpecCardEntry.test.ts src/modules/pod_customization/data/podCustomizationModel.test.ts src/modules/pod_customization/data/podCustomizationDraft.test.ts`

Expected: PASS.

### Task 5: Full Regression and Verification

**Files:**
- Verify all files modified in Tasks 1–4.

**Interfaces:**
- Consumes the completed backend and frontend behavior.
- Produces no new production interface.

- [ ] **Step 1: Run backend POD regressions**

Run: `cd local-runtime && python -m pytest wh_local/modules/pod_customization/tests/test_spec_card.py wh_local/modules/pod_customization/tests/test_spec_card_service.py wh_local/modules/pod_customization/tests/test_worker_unit.py wh_local/modules/pod_customization/tests/test_dianxiaomi_export.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend module tests**

Run: `cd web-frontend && npm test -- --runInBand`

Expected: PASS, or use the repository's actual test script shown by `npm run` if it does not accept `--runInBand`.

- [ ] **Step 3: Run type/build verification**

Run: `cd web-frontend && npm run build`

Expected: successful TypeScript/Vite build.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only the planned feature files plus the user's pre-existing changes are present.
