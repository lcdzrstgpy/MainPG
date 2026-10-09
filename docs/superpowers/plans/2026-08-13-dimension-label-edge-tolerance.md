# Dimension Label Edge Tolerance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow dimension labels at every canvas edge while preserving complete, unclipped exported text.

**Architecture:** Keep normalized coordinate validation in `DimensionRenderer`, but replace the 5% label-center admission rule with render-time bounding-box clamping. Use a 0.5% output padding and move only the text center; dimension-line endpoints and all revision/publication behavior remain unchanged.

**Tech Stack:** Python 3.14, Pillow, Pydantic, pytest.

---

## File map

- Modify `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py`: remove the label-center safe-zone rejection and clamp the rendered text box to a 0.5% export padding.
- Modify `local-runtime/tests/test_product_processing_dimension_renderer.py`: cover all four edges, corners, invalid normalized coordinates, unchanged centered placement, and oversized labels.

### Task 1: Relax label admission and preserve export bounds

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py`
- Test: `local-runtime/tests/test_product_processing_dimension_renderer.py`

- [ ] **Step 1: Write failing edge-position tests**

Replace the old rejection case for `label=(0.01, 0.5)` with parameterized successful renders:

```python
@pytest.mark.parametrize(
    "label",
    [(0.0, 0.5), (1.0, 0.5), (0.5, 0.0), (0.5, 1.0), (0.0, 0.0), (1.0, 1.0)],
)
def test_renderer_accepts_labels_at_canvas_edges(label: tuple[float, float]) -> None:
    output = DimensionRenderer().render(
        DimensionRenderRequest(
            source_bytes=_source_bytes(),
            annotations=[_length_annotation(label=label)],
        )
    )
    assert output.width == 2000
    assert output.height == 2000
```

Add a direct helper assertion proving a centered label remains unchanged and an edge label moves only enough to fit:

```python
def test_label_fitting_preserves_center_and_clamps_edge() -> None:
    image = Image.new("RGB", (2000, 2000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(_FONT_PATH), 88)
    centered = _fit_label_inside_safe_margin(
        draw, (1000, 1000), "10 cm", font=font, stroke_width=5, size=2000
    )
    edge = _fit_label_inside_safe_margin(
        draw, (0, 1000), "10 cm", font=font, stroke_width=5, size=2000
    )
    assert centered == (1000, 1000)
    assert edge[0] > 0
```

- [ ] **Step 2: Run the focused test and verify the old 5% rejection fails**

Run through the task-local, evolution-gated PowerShell test script:

```powershell
$env:PYTHONPATH='C:\Users\小风\Documents\ChatGPT\启凡电商工作台\local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest `
  'C:\Users\小风\Documents\ChatGPT\启凡电商工作台\local-runtime\tests\test_product_processing_dimension_renderer.py' -q
```

Expected: edge cases fail with `dimension_label_outside_safe_margin` before implementation.

- [ ] **Step 3: Implement minimal render-time clamping**

Change the renderer constant and validation:

```python
_EXPORT_PADDING_RATIO = 0.005

# Keep the existing 0..1 finite-coordinate validation for start/end/label.
# Delete the separate `_SAFE_MARGIN_RATIO <= label <= 1 - _SAFE_MARGIN_RATIO` block.
```

Inside `_fit_label_inside_safe_margin`, calculate:

```python
safe = max(stroke_width + 1, round(size * _EXPORT_PADDING_RATIO))
```

Retain the existing real text-bounds size check and post-adjustment bounds verification. These remain the only source of `dimension_label_outside_safe_margin`.

- [ ] **Step 4: Add invalid-coordinate and oversized-label regressions**

Keep or add parameterized Pydantic validation for labels outside the normalized canvas:

```python
@pytest.mark.parametrize("label", [(-0.001, 0.5), (1.001, 0.5)])
def test_renderer_rejects_labels_outside_normalized_canvas(label) -> None:
    with pytest.raises(ValidationError):
        _length_annotation(label=label)
```

Use a mocked `textbbox` whose width exceeds the padded canvas and assert the helper still raises `dimension_label_outside_safe_margin`.

- [ ] **Step 5: Run focused and complete backend tests**

Run the focused renderer test, then:

```powershell
$env:PYTHONPATH='C:\Users\小风\Documents\ChatGPT\启凡电商工作台\local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest `
  'C:\Users\小风\Documents\ChatGPT\启凡电商工作台\local-runtime\tests' -q
```

Expected: all tests pass without network, COS, browser, or store writes.

- [ ] **Step 6: Verify and commit the scoped hotfix**

Run `git diff --check`, confirm the unrelated untracked precheck plan remains unstaged, then stage only the renderer and renderer test:

```powershell
git add -- local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py local-runtime/tests/test_product_processing_dimension_renderer.py
git commit -m "fix(dimension-canvas): allow labels at image edges"
```
