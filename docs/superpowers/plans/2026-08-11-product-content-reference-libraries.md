# Product Content Reference Libraries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add large deterministic title and image content-reference libraries that consume existing confirmed category/attribute results without changing category matching, Dianxiaomi export, hard title rules, or four-grid image processing.

**Architecture:** A new pure domain module owns 43 sourced category profiles, 9 MainPG-specific overrides, stable selection, attribute-triggered modules, and bounded prompt appendices. `service.py` appends the selected content-only reference after the existing prompt is fully rendered; all existing prompts and execution stages remain authoritative and no new provider call is introduced.

**Tech Stack:** Python 3.14, dataclasses, hashlib, pytest, existing FastAPI product-processing module.

---

## File Map

- Create `local-runtime/wh_local/modules/product_processing/domain/content_reference_library.py`: immutable 43-category catalog, stable selector, prompt appendix renderer.
- Create `local-runtime/wh_local/modules/product_processing/domain/content_reference_sources.json`: pinned GitHub sources, licenses, counts, and transformation policy.
- Create `local-runtime/wh_local/modules/product_processing/THIRD_PARTY_NOTICES.md`: source attribution and redistribution notes.
- Modify `local-runtime/wh_local/modules/product_processing/service.py`: append title/image references at four existing generation seams and record reference IDs.
- Create `local-runtime/tests/test_product_processing_content_references.py`: pure selector/catalog/safety tests.
- Create `local-runtime/tests/test_product_processing_reference_integration.py`: prompt-preservation and service-seam regression tests.

### Task 1: Lock the catalog and deterministic selector contract

**Files:**
- Create: `local-runtime/tests/test_product_processing_content_references.py`
- Create: `local-runtime/wh_local/modules/product_processing/domain/content_reference_library.py`

- [ ] **Step 1: Write the failing catalog and determinism tests**

```python
from copy import deepcopy

from wh_local.modules.product_processing.domain.content_reference_library import (
    CATEGORY_PROFILES,
    append_content_reference,
    select_image_reference,
    select_title_reference,
)


def _raw(product_id: str = "offer-1") -> dict:
    return {
        "source_product_id": product_id,
        "category_id": "12345",
        "category_path": "Home & Kitchen > Kitchen & Dining > Drinkware",
        "source_attributes": [
            {"attribute_name_en": "Capacity", "value_name_en": "500 ml"},
            {"attribute_name_en": "Material", "value_name_en": "Stainless Steel"},
        ],
    }


def test_catalog_has_43_sourced_categories_project_overrides_and_general() -> None:
    assert len(CATEGORY_PROFILES) == 53
    assert "general" in CATEGORY_PROFILES


def test_same_product_selects_same_references_without_mutating_input() -> None:
    raw = _raw()
    before = deepcopy(raw)
    first = select_title_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")
    second = select_title_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")
    assert first == second
    assert raw == before


def test_different_products_spread_across_vetted_variants() -> None:
    ids = {
        select_title_reference(_raw(f"offer-{index}"), title="Insulated Travel Mug", category="Kitchen & Dining").reference_id
        for index in range(32)
    }
    assert len(ids) >= 4


def test_unknown_category_falls_back_without_failure() -> None:
    reference = select_image_reference(
        {"source_product_id": "unknown-1", "category_path": "Unmapped Leaf"},
        title="Plain Item",
        category="Unmapped Leaf",
    )
    assert reference.profile_id == "general"
    assert reference.text


def test_prompt_appendix_is_content_only_and_bounded() -> None:
    reference = select_image_reference(_raw(), title="Insulated Travel Mug", category="Kitchen & Dining")
    prompt = append_content_reference("BASE HARD RULES", reference, kind="image")
    assert prompt.startswith("BASE HARD RULES")
    assert "CONTENT REFERENCE ONLY" in prompt
    assert len(prompt) <= len("BASE HARD RULES") + 1800
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -X utf8 -m pytest tests/test_product_processing_content_references.py -q` from `local-runtime`.

Expected: collection fails because `content_reference_library` does not exist.

- [ ] **Step 3: Implement the immutable model and stable-selection API**

```python
@dataclass(frozen=True, slots=True)
class CategoryProfile:
    profile_id: str
    aliases: tuple[str, ...]
    title_priorities: tuple[str, ...]
    visual_focus: str
    scene_roles: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class ContentReference:
    kind: str
    profile_id: str
    variant_id: str
    text: str

    @property
    def reference_id(self) -> str:
        return f"{self.profile_id}/{self.variant_id}"


def _stable_index(raw: Mapping[str, Any], title: str, category: str, size: int, salt: str) -> int:
    identity = "|".join(
        str(value or "").strip()
        for value in (
            raw.get("category_id") or raw.get("leaf_category_id"),
            raw.get("category_path") or raw.get("source_category_path") or category,
            raw.get("source_product_id") or raw.get("product_id") or raw.get("offer_id")
            or raw.get("candidate_id") or raw.get("skc") or raw.get("sku"),
            title,
            salt,
        )
    )
    return int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big") % size
```

Add exactly these sourced profile IDs, the project-specific IDs below, plus `general`:

```text
air-purifiers-home-tech, fashion-apparel, art, auto-moto-accessories,
baby-kids, bags-accessories, bedding-bath, beer-spirits, beverages,
books-media, cleaning-household, coffee, crafts-hobby, electronics,
equine, essential-oils, eyewear, footwear, fragrance-home-scent,
furniture, garden-outdoor-living, greeting-cards-gifts, haircare,
health-food, home-decor, home-improvement-diy, jewelry, kitchen-dining,
makeup, nail-care, office-stationery, personal-care, pet-food-supplies,
plants-flowers, skincare, specialty-food, sports-fitness, supplements,
tea-matcha, toys-games, watches, wine, workwear-safety
```

Project-specific IDs: `musical-tools-accessories`, `tools-hardware`, `home-storage-organization`, `table-linen`, `soft-home-textile`, `lighting-electrical`, `party-festival`, `beauty-personal-accessory`, `packaging-bags`.

Each row must have English and Chinese category aliases, a category-specific title priority tuple, one factual visual focus, and four different scene roles. Matching must use only `category`, `category_path`, and `source_category_path`; title/attributes may influence modules but must never select or rewrite the category.

- [ ] **Step 4: Add eight title arrangements, eight visual treatments, and evidence-triggered modules**

```python
TITLE_ARRANGEMENTS = (
    "Lead with the exact product type, then the strongest verified differentiator, then supporting size/count/use facts.",
    "Lead with the exact product type and verified construction, then size/capacity, then a supported use context.",
    "Lead with the exact product type, then verified form/style, then compatibility or intended use, then quantity.",
    "Lead with the exact product type and the shopper's clearest verified selection attribute, then secondary facts.",
    "Lead with the exact product type, then a verified performance-relevant feature, then physical specification.",
    "Lead with the exact product type, then verified material/finish, then form factor and real pack contents.",
    "Lead with the exact product type and model/fit when present, then verified function and size/count.",
    "Lead with the exact product type, then two complementary verified attributes, ending with a supported use case.",
)

VISUAL_TREATMENTS = (
    ("clean catalog realism", "soft diffused studio light", "quiet neutral surface"),
    ("bright everyday realism", "large natural window light", "light lived-in setting"),
    ("material-led close realism", "soft side light with controlled highlights", "simple tactile surface"),
    ("functional demonstration realism", "even directional light", "credible use environment"),
    ("refined minimal editorial realism", "gentle key and rim light", "muted tonal background"),
    ("fresh airy commercial realism", "high-key diffused light", "subtle category-relevant background"),
    ("warm practical lifestyle realism", "warm natural side light", "uncluttered home or work context"),
    ("precise detail-forward realism", "controlled macro-friendly light", "plain contrasting surface"),
)
```

Attribute modules are keyed by observed labels/values for material, size, capacity, quantity, compatibility, color/pattern, closure/construction, power/control, care, and set/bundle. Render at most two modules and never inject a value that is absent from source evidence.

- [ ] **Step 5: Run the pure tests**

Run: `python -X utf8 -m pytest tests/test_product_processing_content_references.py -q`.

Expected: all tests pass.

### Task 2: Preserve hard prompts while appending title and image references

**Files:**
- Create: `local-runtime/tests/test_product_processing_reference_integration.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`

- [ ] **Step 1: Write prompt-preservation regression tests**

```python
from pathlib import Path

from wh_local.modules.product_processing.domain.content_reference_library import (
    append_content_reference,
    select_image_reference,
    select_title_reference,
)
from wh_local.modules.product_processing.domain.prompts import COMBINED_TEXT_PROMPT, GRID_IMAGE_PROMPT


def test_title_reference_keeps_existing_hard_boundaries_verbatim() -> None:
    raw = {"category_path": "Jewelry > Earrings", "source_product_id": "p-1"}
    reference = select_title_reference(raw, title="Flower Earrings", category="Jewelry")
    rendered = append_content_reference(COMBINED_TEXT_PROMPT, reference, kind="title")
    assert "Ideal length is 60-130 characters" in rendered
    assert "Hard maximum 180 characters" in rendered
    assert "Do not invent material" in rendered


def test_image_reference_keeps_exact_four_grid_rules_verbatim() -> None:
    raw = {"category_path": "Home > Storage", "source_product_id": "p-2"}
    reference = select_image_reference(raw, title="Storage Basket", category="Home Storage")
    rendered = append_content_reference(GRID_IMAGE_PROMPT, reference, kind="image")
    assert "exact four-panel 2x2" in rendered
    assert "Do not change the four-grid structure" in rendered
    assert "Do not change the product itself" in rendered


def test_dianxiaomi_export_module_is_not_modified_by_feature() -> None:
    service_source = Path("wh_local/modules/product_processing/service.py").read_text(encoding="utf-8")
    assert "select_title_reference" in service_source
    assert "select_image_reference" in service_source
```

- [ ] **Step 2: Run the tests and verify service integration is absent**

Run: `python -X utf8 -m pytest tests/test_product_processing_reference_integration.py -q` from `local-runtime`.

Expected: the source integration assertion fails.

- [ ] **Step 3: Add the four narrow service seams**

Import the selector and appendix helpers:

```python
from .domain.content_reference_library import (
    append_content_reference,
    select_image_reference,
    select_title_reference,
)
```

At `_generate_combined_text` and `_generate_title`, after `format_prompt(...)`:

```python
reference = select_title_reference(raw, title=source_title, category=category)
prompt = append_content_reference(prompt, reference, kind="title")
self._note_content_reference(ai_notes, "title_reference", reference.reference_id)
```

At `_generate_grid_images` and `_generate_detail_images`, after `format_prompt(...)`:

```python
reference = select_image_reference(raw, title=optimized_title, category=category)
prompt = append_content_reference(prompt, reference, kind="image")
self._note_content_reference(ai_notes, "image_reference", reference.reference_id)
```

Add a duplicate-safe note helper:

```python
@staticmethod
def _note_content_reference(ai_notes: list[str] | None, label: str, reference_id: str) -> None:
    note = f"{label}:{reference_id}"
    if ai_notes is not None and note not in ai_notes:
        ai_notes.append(note)
```

Do not modify `_generate_size`, `_translate_variant_values`, `_repair_until_clean`, `split_four_grid`, `domain/workbooks.py`, API schemas, or category fields.

- [ ] **Step 4: Run both focused suites**

Run: `python -X utf8 -m pytest tests/test_product_processing_content_references.py tests/test_product_processing_reference_integration.py -q`.

Expected: all tests pass.

### Task 3: Add source provenance and safety gates

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/domain/content_reference_sources.json`
- Create: `local-runtime/wh_local/modules/product_processing/THIRD_PARTY_NOTICES.md`
- Modify: `local-runtime/tests/test_product_processing_content_references.py`

- [ ] **Step 1: Add the pinned source manifest**

The JSON must contain ten entries with `repository`, `commit`, `license`, `use`, and `runtime_copy` fields, using the exact commits in the approved design spec. `runtime_copy` is `adapted-structure` for MIT sources and `normalized-content-elements` for the CC0 source.

- [ ] **Step 2: Add notices without bundling unlicensed repositories**

Document each included repository, license, fixed commit, transformed scope, and that no-license search results were excluded from redistribution. State that MainPG hard constraints override every external reference.

- [ ] **Step 3: Add manifest and forbidden-content tests**

```python
import json
import re
from pathlib import Path


def test_source_manifest_is_pinned_and_permissively_licensed() -> None:
    path = Path("wh_local/modules/product_processing/domain/content_reference_sources.json")
    sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
    assert len(sources) == 10
    assert all(re.fullmatch(r"[0-9a-f]{40}", item["commit"]) for item in sources)
    assert {item["license"] for item in sources} <= {"MIT", "CC0-1.0"}


def test_runtime_reference_text_excludes_external_hard_controls_and_claims() -> None:
    forbidden = re.compile(
        r"amazon|ozon|temu|\b\d{2,4}\s*(?:px|characters?)\b|\b\d\s*[x×]\s*\d\b|"
        r"best seller|five stars|review count|money-back|certified|discount|free shipping",
        re.IGNORECASE,
    )
    for profile in CATEGORY_PROFILES.values():
        corpus = " ".join((*profile.title_priorities, profile.visual_focus, *profile.scene_roles))
        assert forbidden.search(corpus) is None, profile.profile_id
```

- [ ] **Step 4: Run validation and syntax checks**

Run from `local-runtime`:

```text
python -X utf8 -m pytest tests/test_product_processing_content_references.py tests/test_product_processing_reference_integration.py -q
python -X utf8 -m py_compile wh_local/modules/product_processing/domain/content_reference_library.py wh_local/modules/product_processing/service.py
```

Expected: all tests pass and `py_compile` exits zero.

### Task 4: Final regression and workspace handoff

**Files:**
- Verify only; do not stage or commit.

- [ ] **Step 1: Verify no protected subsystem changed**

Run from repository root:

```text
git diff --name-only
git diff -- local-runtime/wh_local/modules/product_processing/domain/workbooks.py
git diff -- local-runtime/wh_local/modules/product_processing/api
git diff -- web-frontend
```

Expected: the last three diffs are empty.

- [ ] **Step 2: Run repository hygiene checks**

Run:

```text
git diff --check
git status --short
```

Expected: no whitespace errors; only the design, plan, library, manifest, notice, service, and two test files appear.

- [ ] **Step 3: Report without committing**

Report the catalog size, deterministic diversity, source licenses, focused test results, protected files unchanged, and exact uncommitted workspace state. Do not run `git add`, `git commit`, `git push`, deployment, provider calls, or Dianxiaomi imports.
