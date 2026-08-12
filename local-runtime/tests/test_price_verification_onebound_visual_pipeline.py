from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wh_local.price_verification.contracts import PriceVerificationActor
from wh_local.price_verification.repository import PriceVerificationRepository
from wh_local.price_verification.sourcing.contracts import SourceSearchTask
from wh_local.price_verification.sourcing.onebound_adapter import OneBoundSourceAdapter


@dataclass
class _Result:
    response: dict[str, Any]
    audits: tuple[object, ...] = ()
    error: object | None = None


class _Provider:
    def __init__(self) -> None:
        self.image_target_count = 0

    def search_by_image(self, criteria: object) -> _Result:
        self.image_target_count = int(getattr(criteria, "target_count"))
        items = [
            {
                "num_iid": str(111111 + index),
                "title": "宠物降温冰垫",
                "pic_url": f"https://images.example/search-thumb-{index}.jpg",
                "item_url": f"https://detail.1688.com/offer/{111111 + index}.html",
            }
            for index in range(5)
        ]
        return _Result(
            response={
                "items": {
                    "item": items
                }
            }
        )

    def search_keyword(self, criteria: object) -> _Result:
        return _Result(response={"items": {"item": []}})

    def get_item_detail(self, offer_id: str) -> _Result:
        assert offer_id == "111111"
        return _Result(
            response={
                "item": {
                    "num_iid": "111111",
                    "title": "宠物降温冰垫详情",
                    "pic_url": "https://images.example/detail-gallery.jpg",
                    "price": "8.8",
                }
            }
        )


def test_adapter_recalls_sixty_and_keeps_verified_thumbnail_with_offer(monkeypatch: Any) -> None:
    provider = _Provider()

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert reference_url == "https://images.example/temu.jpg"
        assert len(candidates) == 5
        output = [dict(candidates[0])]
        output[0].update(
            image_similarity_score=0.96,
            image_similarity_method="test",
            image_similarity_verified=True,
        )
        return output, {"reference_available": True, "verified_count": 1, "input_count": 1}

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-1",
        skc_id="skc-1",
        main_image_url="https://images.example/temu.jpg",
        source_quote_keys=("quote-1",),
        product_title="Pet cooling mat",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    candidate = result["items"][0]["candidates"][0]
    assert provider.image_target_count == 60
    assert candidate["num_iid"] == "111111"
    assert candidate["main_image_url"] == "https://images.example/search-thumb-0.jpg"
    assert candidate["image_similarity_score"] == 0.96
    assert len(result["items"][0]["candidates"]) == 5


def test_title_conflicts_do_not_empty_visual_recall(monkeypatch: Any) -> None:
    provider = _Provider()

    def verify(reference_url: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert reference_url == "https://images.example/temu-vest.jpg"
        assert len(candidates) == 5
        # "Cooling" may be absent from Chinese 1688 titles, but every image
        # hit must still reach visual verification.
        assert all("title_evidence_status" in candidate for candidate in candidates)
        output = []
        for index, candidate in enumerate(candidates):
            item = dict(candidate)
            item.update(
                image_similarity_score=0.9 - index / 100,
                image_similarity_method="test",
                image_similarity_verified=True,
                image_similarity_fallback=False,
            )
            output.append(item)
        return output, {
            "reference_available": True,
            "verified_count": 5,
            "input_count": len(candidates),
            "fallback_count": 0,
        }

    monkeypatch.setattr(
        "wh_local.price_verification.sourcing.onebound_adapter.verify_visual_candidates",
        verify,
    )
    repository = object.__new__(PriceVerificationRepository)
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="skc-vest",
        skc_id="55872375182",
        main_image_url="https://images.example/temu-vest.jpg",
        source_quote_keys=("55872375182",),
        product_title="Cute Cartoon Dog Vest, Cooling Breathable Pet Clothing",
        max_candidates=5,
    )

    result = adapter.search_by_image(
        PriceVerificationActor(workspace_id="workspace", actor_id="employee"),
        (task,),
        keyword_search=False,
    )

    item = result["items"][0]
    assert item["visual_verification"]["input_count"] == 5
    assert sum(item["visual_verification"]["title_evidence"].values()) == 5
    assert len(item["candidates"]) == 5
