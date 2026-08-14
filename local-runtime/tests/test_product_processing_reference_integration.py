from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import wh_local.modules.product_processing.service as service_module

from wh_local.modules.product_processing.domain.content_reference_library import (
    append_content_reference,
    select_image_reference,
    select_title_reference,
)
from wh_local.modules.product_processing.domain.prompts import (
    COMBINED_TEXT_PROMPT,
    GRID_IMAGE_PROMPT,
    default_image_context,
    format_prompt,
)
from wh_local.modules.product_processing.service import ProductProcessingService


LOCAL_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LOCAL_RUNTIME_ROOT.parent
SERVICE_PATH = LOCAL_RUNTIME_ROOT / "wh_local/modules/product_processing/service.py"


def _raw() -> dict:
    return {
        "source_product_id": "integration-offer-1",
        "category_id": "12345",
        "category_path": "Home & Kitchen > Kitchen & Dining > Drinkware",
        "source_attributes": [
            {"attribute_name_en": "Capacity", "value_name_en": "500 ml"},
            {"attribute_name_en": "Material", "value_name_en": "Stainless Steel"},
        ],
    }


def _call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else ""


def test_title_reference_append_preserves_combined_text_hard_rules() -> None:
    raw = _raw()
    prompt = format_prompt(
        COMBINED_TEXT_PROMPT,
        title="500 ml Stainless Steel Travel Mug",
        category="Kitchen & Dining",
        category_path=raw["category_path"],
        required_attributes="Capacity: 500 ml; Material: Stainless Steel",
        matched_terms="travel mug, drinkware",
        value_evidence="Source title and structured attributes only.",
        verified_material_evidence="Verified structured source attribute: Stainless Steel.",
        variant_options="[]",
        target_language_name="English",
        language_code="en",
    )
    reference = select_title_reference(
        raw,
        title="500 ml Stainless Steel Travel Mug",
        category="Kitchen & Dining",
    )

    combined = append_content_reference(prompt, reference, kind="title")

    assert combined.startswith(prompt.rstrip())
    assert "CONTENT REFERENCE ONLY — TITLE:" in combined
    assert "around 180 English letters" in combined
    assert "Never exceed 200 letters" in combined
    assert "The title must identify the exact product being sold" in combined
    assert "Product identity accuracy is more important than length or SEO breadth" in combined
    assert "Do not invent material, certification, function, compatibility, quantity, size, scene" in combined


def test_image_reference_append_preserves_exact_grid_and_product_fidelity_rules() -> None:
    raw = _raw()
    context = default_image_context(
        "500 ml Stainless Steel Travel Mug",
        "Home & Kitchen > Kitchen & Dining > Drinkware",
        material_evidence="Verified structured source attribute: Stainless Steel.",
    )
    prompt = format_prompt(GRID_IMAGE_PROMPT, **context)
    reference = select_image_reference(
        raw,
        title="500 ml Stainless Steel Travel Mug",
        category="Kitchen & Dining",
    )

    combined = append_content_reference(prompt, reference, kind="image")

    assert combined.startswith(prompt.rstrip())
    assert "CONTENT REFERENCE ONLY — IMAGE:" in combined
    assert "ONLY source of truth for the SKU" in combined
    assert "exact four-panel 2x2 e-commerce grid" in combined
    assert "Keep an exact four-panel 2x2 grid with clean straight dividers" in combined
    assert "Do not change the four-grid structure, divider layout, or split logic" in combined
    assert "Lock before generating" in combined
    assert "Do not change the product itself" in combined


def test_service_has_exactly_four_narrow_content_reference_integration_points() -> None:
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH))
    service_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductProcessingService"
    )
    methods = {
        node.name: node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = {
        "_generate_combined_text": "select_title_reference",
        "_generate_title": "select_title_reference",
        "_generate_grid_images": "select_image_reference",
        "_generate_detail_images": "select_image_reference",
        "_generate_premium_images": "select_image_reference",
    }

    for method_name, selector_name in expected.items():
        calls = Counter(
            _call_name(node)
            for node in ast.walk(methods[method_name])
            if isinstance(node, ast.Call)
        )
        assert calls[selector_name] == 1, method_name
        assert calls["append_content_reference"] == 1, method_name

    integration_owners: dict[str, list[str]] = {
        "select_title_reference": [],
        "select_image_reference": [],
        "append_content_reference": [],
    }
    for method_name, method in methods.items():
        called = [
            _call_name(node)
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
        ]
        for integration_name in integration_owners:
            integration_owners[integration_name].extend(
                method_name for call_name in called if call_name == integration_name
            )

    assert integration_owners == {
        "select_title_reference": ["_generate_combined_text", "_generate_title"],
        "select_image_reference": [
            "_generate_grid_images",
            "_generate_premium_images",
            "_generate_detail_images",
        ],
        "append_content_reference": [
            "_generate_combined_text",
            "_generate_title",
            "_generate_grid_images",
            "_generate_premium_images",
            "_generate_detail_images",
        ],
    }


def test_reference_library_does_not_leak_into_workbook_api_or_frontend() -> None:
    workbook = LOCAL_RUNTIME_ROOT / "wh_local/modules/product_processing/domain/workbooks.py"
    api_files = sorted(
        (LOCAL_RUNTIME_ROOT / "wh_local/modules/product_processing/api").rglob("*.py")
    )
    frontend_files = sorted(
        path
        for path in (PROJECT_ROOT / "web-frontend/src").rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}
    )
    targets = [workbook, *api_files, *frontend_files]
    forbidden = (
        "content_reference_library",
        "select_title_reference",
        "select_image_reference",
        "append_content_reference",
    )

    assert workbook.is_file()
    assert api_files
    assert frontend_files
    leaks = {
        str(path.relative_to(PROJECT_ROOT)): marker
        for path in targets
        for marker in forbidden
        if marker in path.read_text(encoding="utf-8")
    }
    assert leaks == {}


class _PromptRepository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {}


class _CapturingTextClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, messages: list[dict]) -> str:
        self.prompts.append(messages[0]["content"])
        return json.dumps(
            {
                "optimized_title": "Insulated Travel Mug Stainless Steel 500 ml",
                "description": "\n".join(
                    [
                        "- VERIFIED STEEL BUILD: Confirmed stainless steel construction supports dependable everyday drink service while preserving the source-supported product identity and clean travel mug shape.",
                        "- PRACTICAL DRINK USE: The mug is suited to routine beverage use at a desk, during a commute, or in other ordinary daily settings.",
                        "- CONFIRMED CAPACITY SIZE: The verified 500 ml capacity provides a clear volume reference without adding unsupported dimensions or package claims.",
                        "- SIMPLE DAILY HANDLING: The straightforward mug format is easy to place, carry, and incorporate into a regular beverage routine with normal care.",
                        "- USEFUL PORTABLE FORMAT: Its travel-oriented form combines the confirmed capacity and construction in one practical item for supported on-the-go use.",
                    ]
                ),
                "variant_translations": [],
            }
        )


def test_combined_text_reference_does_not_add_provider_calls(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    client = _CapturingTextClient()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service, "_ai_client", lambda: client)
    monkeypatch.setattr(service, "_load_ai_stage_cache", lambda stage, key: None)
    monkeypatch.setattr(service, "_save_ai_stage_cache", lambda *args, **kwargs: None)
    notes: list[str] = []

    result = service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        _raw(),
        "en",
        "US",
        notes,
    )

    assert result is not None
    assert len(client.prompts) == 1
    assert "CONTENT REFERENCE ONLY — TITLE:" in client.prompts[0]
    assert "around 180 English letters" in client.prompts[0]
    assert sum(note.startswith("title_reference:") for note in notes) == 1


class _CapturingImageProcessor:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.layout_scaffold_values: list[bool] = []
        self.image_sizes: list[str | None] = []

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: list[str],
        layout_scaffold: bool = False,
        image_size: str | None = None,
    ) -> SimpleNamespace:
        assert stage == "grid_image"
        assert reference_values == ["https://example.com/source.jpg"]
        self.prompts.append(prompt)
        self.layout_scaffold_values.append(layout_scaffold)
        self.image_sizes.append(image_size)
        return SimpleNamespace(stage=stage, content=b"image", content_type="image/png", attempt_count=1)

    @staticmethod
    def split_four_grid(media: SimpleNamespace) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(stage="grid_image_1", content=b"one"),
            SimpleNamespace(stage="grid_image_2", content=b"two"),
            SimpleNamespace(stage="grid_image_3", content=b"three"),
            SimpleNamespace(stage="grid_image_4", content=b"four"),
            SimpleNamespace(stage="grid_image_summary", content=b"summary"),
        ]


def test_grid_image_reference_does_not_add_provider_calls(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    processor = _CapturingImageProcessor()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": []},
    )
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )
    notes: list[str] = []

    carousel, summary = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        notes,
    )

    assert len(processor.prompts) == 1
    assert "CONTENT REFERENCE ONLY — IMAGE:" in processor.prompts[0]
    assert "exact four-panel 2x2" in processor.prompts[0]
    assert processor.prompts[0].rstrip().endswith(
        "Keep Panel 4 clean for later deterministic dimension annotation."
    )
    # A 模板与 B 模板一致使用固定 2x2 scaffold，保证四等分 + 直线分隔线的结构遵循度
    assert processor.layout_scaffold_values == [True]
    assert processor.image_sizes == ["2048x2048"]
    assert len(carousel) == 4
    assert summary.endswith("grid_image_summary.png")
    assert sum(note.startswith("image_reference:") for note in notes) == 1


def test_b_grid_uses_fixed_scaffold_and_disables_paid_repair(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    processor = _CapturingImageProcessor()
    repair_options: list[bool] = []
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": []},
    )

    def repair(*args, **kwargs):
        repair_options.append(kwargs["allow_paid_repair"])
        return args[3]

    monkeypatch.setattr(service, "_repair_until_clean", repair)
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.png" for part in parts
        ],
    )

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        [],
        image_template="B",
    )

    assert processor.layout_scaffold_values == [True]
    assert processor.image_sizes == ["2048x2048"]
    assert repair_options == []
    assert output.attempt_count == 1
    assert output.provider_status_class == "success"
    assert output.stage_timings_ms.keys() >= {
        "grid_generation_ms",
        "grid_validation_ms",
        "persist_ms",
    }


class _CapturingPremiumImageProcessor:
    """精品模式 mock：记录一次 4K 四宫格调用，不触发任何网络/OCR。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.stages: list[str] = []
        self.image_sizes: list[str | None] = []
        self.models: list[str | None] = []
        self.layout_scaffolds: list[bool] = []

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: list[str],
        layout_scaffold: bool = False,
        image_size: str | None = None,
        model_override: str | None = None,
    ) -> SimpleNamespace:
        assert stage == "premium_image"
        self.prompts.append(prompt)
        self.stages.append(stage)
        self.image_sizes.append(image_size)
        self.models.append(model_override)
        self.layout_scaffolds.append(layout_scaffold)
        return SimpleNamespace(
            stage=stage,
            content=b"image",
            content_type="image/png",
            attempt_count=1,
            provider_status_class="success",
        )

    @staticmethod
    def split_premium_four_grid(_media: SimpleNamespace) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(stage=f"premium_image_{slot}", content=f"slot-{slot}".encode())
            for slot in range(1, 5)
        ] + [SimpleNamespace(stage="premium_image_summary", content=b"summary")]

    def repair_generated(
        self,
        *,
        stage: str,
        prompt: str,
        prior_content: bytes,
        prior_content_type: str,
        reference_values: list[str],
        image_size: str | None = None,
        model: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            stage=stage,
            content=prior_content,
            content_type=prior_content_type,
            attempt_count=1,
            provider_status_class="success",
        )


class _FailingPremiumImageProcessor(_CapturingPremiumImageProcessor):
    """每次 4K 生成都抛错，用于验证最多整体尝试两次。"""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        reference_values: list[str],
        layout_scaffold: bool = False,
        image_size: str | None = None,
        model_override: str | None = None,
    ) -> SimpleNamespace:
        self.attempts += 1
        raise ValueError("simulated premium generation failure")


def _premium_service(monkeypatch, processor) -> ProductProcessingService:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        service,
        "_media_config_provider",
        lambda: {"image": {"premium_image_model": "gpt-image-2-4k", "premium_image_size": "4096x4096"}},
    )
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/premium_{index}.png" for index in range(len(parts))
        ],
    )
    return service


def test_premium_images_generates_one_4k_grid_and_splits_four_high_resolution_images(monkeypatch) -> None:
    """精品模式：一次 4K 四宫格，本地拆四张并保留汇总缩略图。"""
    processor = _CapturingPremiumImageProcessor()
    service = _premium_service(monkeypatch, processor)
    notes: list[str] = []

    output = service._generate_premium_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        notes,
    )

    assert processor.stages == ["premium_image"]
    assert processor.image_sizes == ["4096x4096"]
    assert processor.models == ["gpt-image-2-4k"]
    assert processor.layout_scaffolds == [True]
    assert output.summary_url.endswith("premium_4.png")
    assert len(output.carousel_urls) == 4
    assert all(url.startswith("https://example.com/premium_") for url in output.carousel_urls)
    joined_lower = processor.prompts[0].lower()
    assert "exactly four equal square panels" in joined_lower
    assert "50% vertical center" in joined_lower
    assert "50% horizontal center" in joined_lower
    assert "hero shot:" in joined_lower
    assert "editorial/detail shot" in joined_lower
    assert "lifestyle scene" in joined_lower
    assert "clean front, side, or top view" in joined_lower
    assert sum(note.startswith("image_reference:") for note in notes) == 1


def test_premium_images_never_repeats_a_paid_whole_grid_call(monkeypatch) -> None:
    processor = _FailingPremiumImageProcessor()
    service = _premium_service(monkeypatch, processor)
    notes: list[str] = []

    output = service._generate_premium_images(
        1,
        2,
        _raw(),
        "Insulated Travel Mug Stainless Steel 500 ml",
        "Kitchen & Dining",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        notes,
    )

    assert output.carousel_urls == ()
    assert output.summary_url == ""
    assert processor.attempts == 1
    assert "premium_images:whole_4k_retry" not in notes
    assert any(note.startswith("premium_images:ai-failed:") for note in notes)
