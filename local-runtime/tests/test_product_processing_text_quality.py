from __future__ import annotations

import json

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.ai_client import AiProviderError
from wh_local.modules.product_processing.service import (
    GridImageOutput,
    ListingTextConfigurationError,
    ProductProcessingService,
)


VALID_DESCRIPTION = "\n".join(
    [
        "- VERIFIED STEEL BUILD: Confirmed stainless steel construction supports dependable everyday drink service while preserving the source-supported product identity and clean travel mug shape.",
        "- PRACTICAL DRINK USE: The mug is suited to routine beverage use at a desk, during a commute, or in other ordinary daily settings.",
        "- CONFIRMED CAPACITY SIZE: The verified 500 ml capacity provides a clear volume reference without adding unsupported dimensions or package claims.",
        "- SIMPLE DAILY HANDLING: The straightforward mug format is easy to place, carry, and incorporate into a regular beverage routine with normal care.",
        "- USEFUL PORTABLE FORMAT: Its travel-oriented form combines the confirmed capacity and construction in one practical item for supported on-the-go use.",
    ]
)


def _raw() -> dict:
    return {
        "source_url": "https://example.com/product",
        "source_image_urls": ["https://example.com/source.jpg"],
        "category": "Kitchen & Dining",
        "category_path": "Home & Kitchen > Kitchen & Dining",
        "source_attributes": [
            {"attribute_name_en": "Capacity", "value_name_en": "500 ml"},
            {"attribute_name_en": "Material", "value_name_en": "Stainless Steel"},
        ],
    }


class _PromptRepository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {
            "desc": (
                "CUSTOM OPERATOR DESCRIPTION RULE: emphasize the verified capacity before convenience.\n"
                "Product: {title}\nCategory: {category}\nOutput exactly five English bullet points."
            )
        }


class _CombinedClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts: list[str] = []

    def chat(self, messages: list[dict]) -> str:
        self.prompts.append(messages[0]["content"])
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _combined_service(monkeypatch, response: str | Exception) -> tuple[ProductProcessingService, _CombinedClient]:
    service = object.__new__(ProductProcessingService)
    service.repository = _PromptRepository()
    client = _CombinedClient(response)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service, "_ai_client", lambda: client)
    monkeypatch.setattr(service, "_load_ai_stage_cache", lambda _stage, _key: None)
    monkeypatch.setattr(service, "_save_ai_stage_cache", lambda *args, **kwargs: None)
    return service, client


def test_combined_call_embeds_operator_description_prompt_and_enforces_five_points(monkeypatch) -> None:
    service, client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
                "description": VALID_DESCRIPTION,
                "variant_translations": [],
            }
        ),
    )

    result = service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug",
        "Kitchen & Dining",
        _raw(),
        "en",
        "US",
        [],
    )

    assert result is not None
    assert result["description"].count("\n") == 4
    assert "CUSTOM OPERATOR DESCRIPTION RULE" in client.prompts[0]


def test_combined_invalid_description_is_retained_for_the_single_repair(monkeypatch) -> None:
    candidate = "- CUTE KEYCHAINS: Small decorative charms for bags."
    service, _client = _combined_service(
        monkeypatch,
        json.dumps(
            {
                "optimized_title": "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
                "description": candidate,
                "variant_translations": [],
            }
        ),
    )

    result = service._generate_combined_text(
        "Cartoon Character Keychain Set", "Accessories", _raw(), "en", "US", []
    )

    assert result is not None
    assert result["description"] == ""
    assert result["description_candidate"] == candidate
    assert "exactly five" in result["description_contract_error"]


def test_changing_operator_description_prompt_changes_combined_cache_key(monkeypatch) -> None:
    response = json.dumps(
        {
            "optimized_title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": [],
        }
    )
    service, client = _combined_service(monkeypatch, response)
    cache_keys: list[str] = []

    class _MutableRepository:
        marker = "FIRST DESCRIPTION CONTRACT"

        def prompts(self) -> dict[str, str]:
            return {"desc": f"{self.marker}\nProduct: {{title}}\nOutput exactly five English bullet points."}

    repository = _MutableRepository()
    service.repository = repository
    monkeypatch.setattr(
        service,
        "_load_ai_stage_cache",
        lambda _stage, key: cache_keys.append(key) or None,
    )

    service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug", "Kitchen & Dining", _raw(), "en", "US", []
    )
    repository.marker = "SECOND DESCRIPTION CONTRACT"
    service._generate_combined_text(
        "500 ml Stainless Steel Travel Mug", "Kitchen & Dining", _raw(), "en", "US", []
    )

    assert len(client.prompts) == 2
    assert cache_keys[0] != cache_keys[1]


def test_combined_non_retryable_400_raises_configuration_error(monkeypatch) -> None:
    service, client = _combined_service(
        monkeypatch,
        AiProviderError("AI provider HTTP 400: no routed merchant", status_code=400),
    )

    with pytest.raises(ListingTextConfigurationError, match="no routed merchant"):
        service._generate_combined_text(
            "500 ml Stainless Steel Travel Mug",
            "Kitchen & Dining",
            _raw(),
            "en",
            "US",
            [],
        )
    assert len(client.prompts) == 1


def _process_service(monkeypatch) -> ProductProcessingService:
    service = object.__new__(ProductProcessingService)
    monkeypatch.setattr(service_module, "product_policy_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_identify_subject", lambda *args, **kwargs: ("travel mug", "Travel Mug"))
    return service


def _draft() -> dict:
    return {
        "id": 7,
        "status": "processing",
        "title": "500 ml Stainless Steel Travel Mug",
        "image_url": "https://example.com/source.jpg",
        "raw_payload": _raw(),
    }


def _settings() -> dict:
    return {
        "processing_scope": ["title", "details", "four_grid"],
        "title_optimize": True,
        "ai_media_opt_in": True,
        "image_template": "B",
        "target_language": "en",
        "target_site": "US",
    }


def test_process_stops_after_combined_400_before_any_image_call(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ListingTextConfigurationError("HTTP 400", status_code=400)
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: pytest.fail("image generation must not run after text configuration failure"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "text_provider_configuration"
    assert "stage_timings_ms" in result["result"]


def test_process_rejects_invalid_description_instead_of_exporting_fallback(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": "",
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_generate_description", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: pytest.fail("image generation must not run with invalid description"),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "attention_required"
    assert result["result"]["error_type"] == "description_contract_unmet"
    assert "Source information preserved" not in json.dumps(result, ensure_ascii=False)


def test_process_sends_failed_combined_candidate_to_the_single_description_repair(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    repair_calls: list[dict] = []
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
            "description": "",
            "description_candidate": "- Cute Keychains: Small decorative charms for bags.",
            "description_contract_error": "description must contain exactly five bullet points",
            "variant_translations": {},
        },
    )

    def repaired_description(*args, **kwargs) -> str:
        repair_calls.append(kwargs)
        return VALID_DESCRIPTION

    monkeypatch.setattr(service, "_generate_description", repaired_description)
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert repair_calls[0]["prior_description"].startswith("- Cute Keychains")
    assert repair_calls[0]["contract_error"] == "description must contain exactly five bullet points"
    assert "description_contract:repaired" in result["result"]["ai_notes"]


def test_description_repair_prompt_uses_failed_candidate_as_data_only(monkeypatch) -> None:
    service, client = _combined_service(monkeypatch, VALID_DESCRIPTION)

    result = service._generate_description(
        "Cartoon Character Keychain Set, Colorful Resin Bag Charms with Metal Rings",
        "Accessories",
        _raw(),
        "en",
        "US",
        [],
        prior_description="- Cute Keychains: Small decorative charms for bags.",
        contract_error="description must contain exactly five bullet points",
    )

    assert result.count("\n") == 4
    assert "PREVIOUS CANDIDATE" in client.prompts[0]
    assert "16-24 English words" in client.prompts[0]
    assert "untrusted formatting input only" in client.prompts[0]


def test_process_success_exposes_five_points_grid_attempts_and_stage_timings(monkeypatch) -> None:
    service = _process_service(monkeypatch)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "_generate_combined_text",
        lambda *args, **kwargs: {
            "title": "Insulated Stainless Steel Travel Mug, 500 ml Portable Drink Cup",
            "description": VALID_DESCRIPTION,
            "variant_translations": {},
        },
    )
    monkeypatch.setattr(service, "_translate_variant_values", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_generate_grid_images",
        lambda *args, **kwargs: GridImageOutput(
            carousel_urls=tuple(f"https://example.com/grid-{index}.jpg" for index in range(4)),
            summary_url="https://example.com/grid-summary.jpg",
            attempt_count=1,
            provider_status_class="success",
            stage_timings_ms={
                "grid_generation_ms": 12,
                "grid_validation_ms": 3,
                "publish_ms": 2,
            },
        ),
    )

    result = service._process_one({"id": 1}, _draft(), _settings(), False, task_id=12)

    assert result["status"] == "completed"
    assert result["result"]["description"].count("\n") == 4
    assert len(result["result"]["carousel_image_paths"]) == 4
    assert result["result"]["provider_attempts"] == {"combined_text": 1, "four_grid": 1}
    assert result["result"]["provider_status_classes"]["four_grid"] == "success"
    assert result["result"]["stage_timings_ms"]["grid_generation_ms"] == 12
    assert result["result"]["stage_timings_ms"]["total_processing_ms"] >= 0
