# Product Processing B Grid, Five-Point Description, and Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one paid 2K four-grid call while making B-template panels reliably splittable, bounding failure latency, and refusing any completed item whose description is not a valid factual Amazon-style five-point description.

**Architecture:** Add a focused Pillow-based grid-layout module that builds the fixed 2048 scaffold, validates the exact center, permits only a narrow adaptive split fallback, and rejects internal separator contamination. Keep provider retry classification in the media adapter, description structure in a pure domain module, and orchestration/timing in `ProductProcessingService`; all network-facing behavior is tested through fakes.

**Tech Stack:** Python 3.14, Pillow, requests, FastAPI service layer, SQLAlchemy-backed task results, pytest, TypeScript/Vite verification only.

---

## File map

- Create `local-runtime/wh_local/modules/product_processing/infrastructure/grid_layout.py`: fixed scaffold, split-guide detection, adaptive fallback, square panel extraction, internal-divider rejection.
- Create `local-runtime/wh_local/modules/product_processing/domain/description_contract.py`: normalize and validate exactly five English bullets without inventing facts.
- Modify `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`: attach scaffold to B grid requests, classify provider errors, enforce a 150-second stage budget, expose attempt metadata, delegate splitting to `grid_layout.py`.
- Modify `local-runtime/wh_local/modules/product_processing/domain/prompts.py`: add non-overridable pixel-region contract and remove conflicting 500-character rule.
- Modify `local-runtime/wh_local/modules/product_processing/service.py`: pass B scaffold mode, disable paid B repair, include effective `desc` instructions in combined generation, fail closed on invalid descriptions/configuration errors, record stage timings.
- Modify `local-runtime/tests/test_product_processing_image_quality.py`: prompt/runtime and B no-repair regression coverage.
- Modify `local-runtime/tests/test_product_processing_reference_integration.py`: effective custom description instructions inside combined prompt/cache coverage.
- Create `local-runtime/tests/test_product_processing_grid_layout.py`: synthetic fixed, shifted, multi-grid, internal-divider and scaffold tests.
- Create `local-runtime/tests/test_product_processing_description_contract.py`: valid/invalid five-point normalization tests.
- Create `local-runtime/tests/test_product_processing_pipeline_quality.py`: fake-client 4xx, call-count, fail-closed and timings tests.

### Task 1: Fixed scaffold and safe split module

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/grid_layout.py`
- Create: `local-runtime/tests/test_product_processing_grid_layout.py`

- [ ] **Step 1: Write failing scaffold and fixed-center tests**

Add tests that create a 640×480 red source image, call `build_grid_scaffold()`, and assert:

```python
with Image.open(BytesIO(scaffold)) as image:
    assert image.size == (2048, 2048)
    assert image.getpixel((1024, 100)) == GRID_DIVIDER_RGB
    assert image.getpixel((100, 1024)) == GRID_DIVIDER_RGB
    assert image.getbbox() is not None

guides = locate_split_guides(_exact_grid_bytes())
assert guides == GridSplitGuides(1016, 1032, 1016, 1032, "fixed")
panels = extract_grid_panels(_exact_grid_bytes(), guides)
assert [panel.size for panel in panels] == [(1016, 1016)] * 4
```

- [ ] **Step 2: Run the focused test and verify missing-module failure**

Run:

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_grid_layout.py -q
```

Expected: collection fails because `infrastructure.grid_layout` does not exist.

- [ ] **Step 3: Implement constants, scaffold, and fixed guides**

Implement these public contracts:

```python
GRID_CANVAS_SIZE = 2048
GRID_GUTTER_SIZE = 16
GRID_DIVIDER_RGB = (224, 226, 228)

@dataclass(frozen=True)
class GridSplitGuides:
    x_start: int
    x_end: int
    y_start: int
    y_end: int
    mode: Literal["fixed", "adaptive"]

def build_grid_scaffold(reference_content: bytes) -> bytes:
    source = Image.open(BytesIO(reference_content)).convert("RGB")
    canvas = Image.new("RGB", (GRID_CANVAS_SIZE, GRID_CANVAS_SIZE), (242, 242, 239))
    # Fit, never stretch: each source copy stays inside a 10% inset of its panel.
    tile = ImageOps.contain(source, (812, 812), Image.Resampling.LANCZOS)
    for left, top in ((102, 102), (1134, 102), (102, 1134), (1134, 1134)):
        x = left + (812 - tile.width) // 2
        y = top + (812 - tile.height) // 2
        canvas.paste(tile, (x, y))
    canvas.paste(GRID_DIVIDER_RGB, (1016, 0, 1032, 2048))
    canvas.paste(GRID_DIVIDER_RGB, (0, 1016, 2048, 1032))
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()
```

`locate_split_guides()` first returns the exact fixed guides only when both fixed bands satisfy the existing neutral/light/continuous contract.

- [ ] **Step 4: Write failing adaptive and contamination tests**

Use synthetic 2048 images to assert:

```python
assert locate_split_guides(_grid_at(1000, 1038)).mode == "adaptive"
with pytest.raises(GridLayoutError, match="outside adaptive corridor"):
    locate_split_guides(_grid_at(900, 900))
with pytest.raises(GridLayoutError, match="multiple divider"):
    locate_split_guides(_grid_with_two_vertical_bands())
with pytest.raises(GridLayoutError, match="internal divider"):
    validate_panel_independence(_panel_with_vertical_line_at(165))
```

The shifted fixture must keep both guides within ±51 pixels of center, use 4–24 pixel bands, and keep panel aspect ratios in 0.90–1.10.

- [ ] **Step 5: Implement adaptive guide grouping and panel independence**

Implement a scan that:

1. evaluates only centers `973..1075`;
2. groups adjacent qualifying columns/rows into bands;
3. requires exactly one band per axis, 4–24 pixels wide, ≥95% continuous, neutral-channel spread ≤28 and luminance standard deviation ≤18;
4. rejects opposite panel dimension differences above 102 pixels;
5. center-crops each extracted rectangle to its largest square before later resize;
6. scans each panel's interior 8%–92% for 2–20 pixel low-variance bands spanning ≥80% of the opposite dimension and having sustained contrast on both sides.

Raise `GridLayoutError` for ambiguous or contaminated output; do not return a best guess.

- [ ] **Step 6: Run the layout tests**

Run the command from Step 2. Expected: all grid-layout tests pass.

- [ ] **Step 7: Commit the isolated layout component**

```powershell
git add -- local-runtime/wh_local/modules/product_processing/infrastructure/grid_layout.py local-runtime/tests/test_product_processing_grid_layout.py
git commit -m "feat(product-processing): add fixed four-grid layout contract"
```

### Task 2: Media scaffold request and bounded retry behavior

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`
- Modify: `local-runtime/tests/test_product_processing_image_quality.py`

- [ ] **Step 1: Write failing provider-call-count tests**

Create a fake `_SESSION.post` response and assert:

```python
processor = ProductImageProcessor(lambda: _media_config(image_retry_attempts=3))
with pytest.raises(MediaProcessingError):
    processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=[source_path],
        layout_scaffold=True,
    )
assert post_calls == 1  # HTTP 400 must not be replayed.
```

Add separate fixtures proving one early 500 may retry once, a timeout is not replayed, and the second request is skipped after the 150-second deadline.

- [ ] **Step 2: Run the focused media tests and verify signature failures**

Run:

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_image_quality.py -q
```

Expected: failures because `generate()` lacks `layout_scaffold` and bounded policy inputs.

- [ ] **Step 3: Add attempt metadata and retry classification**

Extend `GeneratedMedia` compatibly with defaults:

```python
attempt_count: int = 1
provider_status_class: str = "success"
```

Extend `MediaProcessingError` so failed attempts remain observable without response bodies:

```python
def __init__(
    self,
    message: str,
    *,
    status_code: int | None = None,
    attempt_count: int = 0,
    status_class: str = "",
):
    super().__init__(message)
    self.status_code = status_code
    self.attempt_count = attempt_count
    self.status_class = status_class
```

Add:

```python
def _retry_class(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    if status in {400, 401, 403, 404}:
        return "non_retryable_4xx"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status < 600:
        return "server_error"
    if isinstance(error, requests.Timeout):
        return "unknown_outcome_timeout"
    if isinstance(error, requests.ConnectionError):
        return "connection_error"
    return "non_retryable_local"
```

For grid mode, use a monotonic 150-second deadline. Retry only `rate_limited`, early `server_error`, and connection-establishment errors, at most once; never replay a request whose outcome is unknown.

- [ ] **Step 4: Attach the scaffold as a structural reference**

Add this keyword parameter to `generate()` and `_generate_with_limits()`:

```python
layout_scaffold: bool = False
```

After loading validated references, build the scaffold from `references[0][0]` and prepend:

```python
scaffold = build_grid_scaffold(references[0][0])
references = [(scaffold, "fixed-four-grid-layout.png", "image/png"), *references]
```

Limit ordinary source references independently so adding the scaffold does not drop the original product identity image.

- [ ] **Step 5: Delegate validation and split extraction**

Replace the fixed-only boxes in `split_four_grid()` with:

```python
guides = locate_split_guides(media.content)
panels = extract_grid_panels(media.content, guides)
for panel in panels:
    validate_panel_independence(panel)
```

Resize each already-square panel to 800×800 with LANCZOS and existing JPEG 94/4:4:4 settings. Keep the fifth summary media unchanged.

- [ ] **Step 6: Run Task 1 and Task 2 tests**

Expected: fixed split, adaptive fallback, contamination rejection, and bounded retry tests pass.

- [ ] **Step 7: Commit media integration**

```powershell
git add -- local-runtime/wh_local/modules/product_processing/infrastructure/media.py local-runtime/tests/test_product_processing_image_quality.py
git commit -m "fix(product-processing): bound B grid generation and splitting"
```

### Task 3: Non-overridable B prompt and no paid quality repair

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/domain/prompts.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/tests/test_product_processing_image_quality.py`

- [ ] **Step 1: Write failing prompt and repair-call tests**

Assert the rendered B prompt contains all exact phrases:

```python
assert "2048 x 2048" in GRID_RUNTIME_CONTRACT
assert "x=1016..1031" in GRID_RUNTIME_CONTRACT
assert "y=1016..1031" in GRID_RUNTIME_CONTRACT
assert "no second full-height or full-width divider" in GRID_RUNTIME_CONTRACT
assert "Do not render these coordinates" in GRID_RUNTIME_CONTRACT
```

Use a fake processor whose generated media fails validation and whose `repair_generated()` increments a counter. Call `_generate_grid_images(..., image_template="B")` and assert `repair_calls == 0`, no publish call occurred, and the result is empty with `four_grid:ai-failed` in notes.

- [ ] **Step 2: Run the failing focused tests**

Use the Task 2 pytest command. Expected: prompt assertions fail and the current service invokes repair.

- [ ] **Step 3: Update the runtime and B prompts**

Set the runtime contract to exact coordinates and add four explicit region ranges. In B Panel 2, replace framed inset language with an integrated detail composition and forbid long internal borders. Keep no-text, product-completeness and non-crossing clauses after all custom/reference prompt text.

- [ ] **Step 4: Make B generation scaffolded and fail-fast**

In `_generate_grid_images()`:

```python
is_b = str(image_template).strip().upper() == "B"
media = processor.generate(
    stage="grid_image",
    prompt=prompt,
    reference_values=reference_urls,
    layout_scaffold=is_b,
)
media = self._repair_until_clean(
    processor,
    "grid_image",
    "four_grid",
    media,
    reference_urls,
    ai_notes,
    allow_paid_repair=not is_b,
)
```

Add `allow_paid_repair: bool = True` to `_repair_until_clean()`. When false and inspection fails, append `four_grid:quality_unresolved` and raise before any provider repair or publication.

- [ ] **Step 5: Keep split media in memory for local detail synthesis**

Before running, add one fake-grid regression that makes every published carousel value an HTTPS URL, replaces
`fetch_public_image` with a function that raises, and verifies local detail synthesis still succeeds from the
in-memory `GeneratedMedia.content` bytes.

Introduce this internal service result:

```python
@dataclass(frozen=True)
class GridImageOutput:
    carousel_urls: tuple[str, ...] = ()
    summary_url: str = ""
    carousel_media: tuple[Any, ...] = ()
```

Return it from `_generate_grid_images()`. Pass `carousel_media` to `_generate_detail_images_local()` and extend
`_local_source_bytes(value)` with these first branches:

```python
if isinstance(value, bytes):
    return value
content = getattr(value, "content", None)
if isinstance(content, bytes):
    return content
```

Only then fall back to local paths or safe HTTP URLs. This removes the current COS upload → HTTP download loop.

- [ ] **Step 6: Run prompt, B no-repair, and in-memory detail tests**

Expected: all focused image-quality tests pass and the fake repair count remains zero.

- [ ] **Step 7: Commit the prompt/runtime contract**

```powershell
git add -- local-runtime/wh_local/modules/product_processing/domain/prompts.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_image_quality.py
git commit -m "fix(product-processing): enforce scaffolded B grid contract"
```

### Task 4: Pure Amazon five-point description contract

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/domain/description_contract.py`
- Create: `local-runtime/tests/test_product_processing_description_contract.py`
- Modify: `local-runtime/wh_local/modules/product_processing/domain/prompts.py`

- [ ] **Step 1: Write failing normalization tests**

Cover `-`, `•`, numbered input and CRLF. A valid fixture must normalize to five `- HEADING: body` lines. Invalid fixtures must raise `DescriptionContractError` for four points, six points, non-uppercase headings, Chinese, duplicate normalized bodies, fewer than 80 or more than 150 words, more than 1000 characters, and `Source information preserved`.

```python
normalized = normalize_five_point_description(VALID_DESCRIPTION)
assert normalized.count("\n") == 4
assert normalized.startswith("- VERIFIED BUILD: ")
with pytest.raises(DescriptionContractError, match="exactly five"):
    normalize_five_point_description(FOUR_POINT_DESCRIPTION)
```

- [ ] **Step 2: Run and verify missing-module failure**

Run:

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_description_contract.py -q
```

- [ ] **Step 3: Implement deterministic parsing and validation**

Expose:

```python
class DescriptionContractError(ValueError):
    pass

def normalize_five_point_description(value: str) -> str:
    lines = _extract_nonempty_bullets(value)
    if len(lines) != 5:
        raise DescriptionContractError("description must contain exactly five bullet points")
    # Parse 2–5 ALL-CAPS words before ':' or ' - ', reject Chinese/internal fallback,
    # reject duplicate normalized bodies, enforce 80–150 ASCII-word tokens and <=1000 chars.
    return "\n".join(normalized_lines)
```

Normalization may change bullet markers and whitespace only. It must never add, merge, paraphrase or invent a selling point.

- [ ] **Step 4: Remove the contradictory prompt length rule**

Change both `DESC_PROMPT` and `COMBINED_TEXT_PROMPT` to exactly five bullets, 80–150 English words total and at most 1000 characters. Add a `{description_instructions}` section to `COMBINED_TEXT_PROMPT` so the service can inject the effective `desc` prompt.

- [ ] **Step 5: Run description tests**

Expected: normalization and rejection matrix passes.

- [ ] **Step 6: Commit the description contract**

```powershell
git add -- local-runtime/wh_local/modules/product_processing/domain/description_contract.py local-runtime/wh_local/modules/product_processing/domain/prompts.py local-runtime/tests/test_product_processing_description_contract.py
git commit -m "feat(product-processing): validate Amazon five-point descriptions"
```

### Task 5: Combined prompt injection, fail-closed orchestration, and timings

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/tests/test_product_processing_reference_integration.py`
- Create: `local-runtime/tests/test_product_processing_pipeline_quality.py`

- [ ] **Step 1: Write failing effective-prompt injection test**

Save a custom `desc` prompt containing `CUSTOM FIVE POINT CONTRACT`, invoke `_generate_combined_text()` with a recording fake client, and assert the user prompt contains that marker. Change the custom prompt and assert `_ai_stage_cache_key()` changes.

- [ ] **Step 2: Write failing pipeline failure tests**

Build a temporary repository/draft and fake clients for these cases:

1. combined call raises `AiProviderError(status_code=400)`;
2. combined returns a valid title but four description points, then the one description-only repair also returns four points;
3. combined returns a valid title and five valid points.

Assert cases 1–2 return `attention_required`, never contain `Source information preserved`, never call image/COS code, and case 1 performs no title/description/variant waterfall. Assert case 3 completes with exactly five normalized lines.

- [ ] **Step 3: Run the focused tests and verify current fail-open behavior**

Run:

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_reference_integration.py local-runtime/tests/test_product_processing_pipeline_quality.py -q
```

Expected: custom `desc` is absent and current fallback description incorrectly completes.

- [ ] **Step 4: Inject the effective description instructions**

Inside `_generate_combined_text()`, render the effective description prompt with the same language/context values, then pass it to the combined template:

```python
description_instructions = format_prompt(
    apply_language_contract_to_prompt(self._effective_prompt("desc"), "desc", target_language, target_site),
    title=source_title,
    image_derived_title=image_derived_title,
    **context,
)
prompt = format_prompt(
    contracted,
    title=source_title,
    image_derived_title=image_derived_title,
    description_instructions=description_instructions,
    variant_options=variant_options_text,
    target_language_name=profile.get("ai_language", target_language),
    language_code=target_language,
    **context,
)
```

Normalize combined and description-only results through `normalize_five_point_description()` before caching or returning them.

- [ ] **Step 5: Preserve structured text failures**

Introduce a configuration exception without changing successful return values:

```python
class ListingTextConfigurationError(RuntimeError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code
```

On HTTP 400/401/403/404, `_generate_combined_text()` raises this exception after recording a safe note.
`_process_one()` catches it and immediately returns `attention_required` with
`failure_class="configuration_blocked"`; it must not execute separate title, description, variant or media calls.
Invalid description content permits exactly one `_generate_description()` call, then fails closed.

- [ ] **Step 6: Record monotonic stage timings in every terminal result**

Use `time.perf_counter()` and one local helper:

```python
stage_timings_ms: dict[str, int] = {}

def timed(stage: str, operation: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return operation()
    finally:
        stage_timings_ms[stage] = max(0, round((time.perf_counter() - started) * 1000))
```

Record the spec stages and `total_processing_ms`; include `stage_timings_ms` and safe `provider_attempts` in completed and attention-required result JSON. Never include prompts, keys or response bodies.

- [ ] **Step 7: Run combined/pipeline tests**

Expected: custom prompt/caching, no-waterfall 4xx, five-point fail-closed and non-negative timings all pass.

- [ ] **Step 8: Commit orchestration changes**

```powershell
git add -- local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_reference_integration.py local-runtime/tests/test_product_processing_pipeline_quality.py
git commit -m "fix(product-processing): fail closed on invalid listing text"
```

### Task 6: Regression suite and local source-backend acceptance

**Files:**
- Modify only if a regression exposes an in-scope defect in files listed above.

- [ ] **Step 1: Run all focused product-processing tests**

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_grid_layout.py local-runtime/tests/test_product_processing_description_contract.py local-runtime/tests/test_product_processing_image_quality.py local-runtime/tests/test_product_processing_reference_integration.py local-runtime/tests/test_product_processing_pipeline_quality.py -q
```

Expected: all focused tests pass with zero network/COS writes.

- [ ] **Step 2: Run the complete backend suite**

```powershell
$env:PYTHONPATH='local-runtime'
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests -q
```

Expected: complete suite passes. Do not switch to the project `.venv` until it actually contains pytest.

- [ ] **Step 3: Build the frontend for contract regressions**

```powershell
npm.cmd --prefix web-frontend run build
```

Expected: TypeScript and Vite build succeeds.

- [ ] **Step 4: Run a fake-provider service smoke**

Create a temporary SQLite database and fake image/text clients, submit one B-template draft through the service, and assert:

```python
assert item["status"] == "completed"
assert len(item["result"]["carousel_image_paths"]) == 4
assert item["result"]["description"].count("\n") == 4
assert item["result"]["stage_timings_ms"]["grid_generation_ms"] >= 0
assert fake_image.calls == 1
assert fake_image.repair_calls == 0
assert fake_publisher.external_calls == 0
```

- [ ] **Step 5: Verify runtime identity without replacing the installed app**

Start the source backend on an unused local test port only after running the restart evolution gate. Verify `/health` reports the repository source path, then stop that test process. Do not stop the user's installed `MainPG.exe`, do not change port 8010, and do not call real providers.

- [ ] **Step 6: Run diff and workspace hygiene checks**

```powershell
git diff --check
git status --short --branch
```

Confirm this task did not stage or modify the pre-existing dimension-canvas work. Remove only task-owned temporary diagnostics.

- [ ] **Step 7: Commit any final in-scope regression fix**

Stage only files listed in this plan and use:

```powershell
git commit -m "test(product-processing): cover B grid quality and latency"
```

Skip this commit when the working tree contains no additional task-owned changes.
