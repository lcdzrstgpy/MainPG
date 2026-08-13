from wh_local.price_verification.quote_normalizer import QuoteItem
from wh_local.price_verification.sourcing.normalizer import normalize_source_candidate
from wh_local.price_verification.sourcing.ranking import rank_candidates_by_image_order
from wh_local.price_verification.sourcing.service import _apply_batch_ranking, _preview_keyword_skc_ids, build_source_preview


def test_image_search_num_iid_and_item_url_remain_linkable_1688_offer() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(skc_id="49301259002"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "折叠收纳箱",
        },
    )

    assert candidate["offer_id"] == "123456789012"
    assert candidate["source_url"] == "https://detail.1688.com/offer/123456789012.html"


def test_image_search_turn_head_is_not_exposed_as_picture_similarity() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(skc_id="49301259002"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "折叠收纳箱",
            "turn_head": "17%",
            "image_search_rank": 2,
        },
    )

    assert "similarity_score" not in candidate
    assert candidate["image_search_rank"] == 2


def test_source_preview_rejects_keyword_only_candidates() -> None:
    preview = build_source_preview(
        [QuoteItem(skc_id="49301259002")],
        {
            "items": [
                {
                    "task_key": "49301259002",
                    "status": "succeeded",
                    "candidates": [
                        {
                            "num_iid": "123456789012",
                            "item_url": "https://detail.1688.com/offer/123456789012.html",
                            "title": "标题搜到的错误商品",
                            "source_channel": "keyword",
                        }
                    ],
                }
            ]
        },
    )

    assert preview["items"][0]["all_candidates"] == []


def test_source_preview_exposes_safe_image_search_audit() -> None:
    preview = build_source_preview(
        [QuoteItem(skc_id="49301259002")],
        {
            "items": [
                {
                    "task_key": "49301259002",
                    "status": "succeeded",
                    "candidates": [],
                    "evidence": [
                        {
                            "operation": "download_reference_image",
                            "response_summary": {
                                "outcome": "success",
                                "final_url": "https://img.temu.test/main.jpeg",
                                "image_size_bytes": 12345,
                            },
                        },
                        {"operation": "upload_img", "response_summary": {"outcome": "success"}},
                        {
                            "operation": "item_search_img",
                            "request_id": "safe-request-id",
                            "captured_at": "2026-08-12T08:00:00+00:00",
                            "response_summary": {"outcome": "success"},
                        },
                    ],
                }
            ]
        },
    )

    assert preview["items"][0]["image_search_audit"] == {
        "downloaded": True,
        "uploaded": True,
        "searched": True,
        "reference_image_url": "https://img.temu.test/main.jpeg",
        "image_size_bytes": 12345,
        "request_id": "safe-request-id",
        "captured_at": "2026-08-12T08:00:00+00:00",
    }


def test_saved_keyword_candidates_are_marked_for_research() -> None:
    assert _preview_keyword_skc_ids(
        {
            "items": [
                {
                    "skc_id": "44980455124",
                    "all_candidates": [{"source_channel": "keyword"}],
                }
            ]
        }
    ) == ("44980455124",)


def test_image_candidates_keep_onebound_return_order_instead_of_price_order() -> None:
    ranked = rank_candidates_by_image_order(
        [
            {"offer_id": "cheap-but-second", "image_search_rank": 2, "price": 1},
            {"offer_id": "first-from-onebound", "image_search_rank": 1, "price": 99},
        ]
    )

    assert [candidate["offer_id"] for candidate in ranked] == ["first-from-onebound", "cheap-but-second"]


def test_image_order_ignores_legacy_title_search_confirmation() -> None:
    ranked = rank_candidates_by_image_order(
        [
            {"offer_id": "wrong-category-first", "image_search_rank": 1},
            {"offer_id": "same-style-confirmed", "image_search_rank": 2, "title_search_confirmed": True},
        ]
    )

    assert [candidate["offer_id"] for candidate in ranked] == ["wrong-category-first", "same-style-confirmed"]


def test_cross_language_category_mismatch_rejects_pet_bowl_for_bamboo_cooling_mat() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(product_title="Bamboo Cooling Mat for Dogs and Cats"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "猫碗陶瓷保护颈椎防打翻猫粮碗实木猫盆猫咪食盆水碗宠物用品",
            "image_search_rank": 2,
        },
    )

    assert candidate["product_evidence_status"] == "conflict"
    assert candidate["product_evidence"] == ["product_category_mismatch"]


def test_cross_language_category_mismatch_rejects_tablecloth_for_cooling_mat() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(product_title="Bamboo Cooling Mat for Dogs and Cats"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "棉麻印花餐桌布防水防烫桌旗家用茶几桌布",
            "image_search_rank": 2,
        },
    )

    assert candidate["product_evidence_status"] == "conflict"
    assert candidate["product_evidence"] == ["product_category_mismatch"]


def test_cross_language_category_mismatch_does_not_infer_temu_category_from_1688_title() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(product_title="Pet Accessory Dog Sleeping Blanket Winter Warm Pet Bed Mat for Small Dogs"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "仿真布垫会叫狗会叫仿真睡狗摆件玩具送人礼品萌物",
            "image_search_rank": 1,
        },
    )

    assert candidate["product_evidence_status"] == "missing"
    assert candidate["product_evidence"] == ["cross_language_title_evidence"]


def test_cross_language_category_mismatch_rejects_non_toy_for_temu_toy() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(product_title="Interactive Dog Toy Doll for Small Pets"),
        {
            "num_iid": "123456789012",
            "item_url": "https://detail.1688.com/offer/123456789012.html",
            "title": "宠物毛毯加厚保暖猫垫子格子地毯法兰绒小型犬被子",
            "image_search_rank": 1,
        },
    )

    assert candidate["product_evidence_status"] == "conflict"
    assert candidate["product_evidence"] == ["product_category_mismatch"]


def test_ranked_preview_keeps_category_conflicts_for_fixed_five_review_display() -> None:
    preview = {
        "items": [
            {
                "skc_id": "30164074709",
                "product_title": "Bamboo Cooling Mat for Dogs and Cats",
                "all_candidates": [
                    {
                        "offer_id": "cat-bowl",
                        "source_title": "猫碗陶瓷保护颈椎防打翻猫粮碗",
                        "product_evidence_status": "conflict",
                        "image_search_rank": 1,
                    }
                ],
            }
        ]
    }

    ranked = _apply_batch_ranking(preview, selections_by_skc={}, ranking_mode="image_order")

    candidates = ranked["items"][0]["all_candidates"]
    assert [candidate["offer_id"] for candidate in candidates] == ["cat-bowl"]
    assert candidates[0]["product_evidence_status"] == "conflict"


def test_ranked_preview_keeps_five_image_hits_even_when_every_title_conflicts() -> None:
    raw_candidates = [
        {
            "num_iid": str(700000000000 + index),
            "item_url": f"https://detail.1688.com/offer/{700000000000 + index}.html",
            "title": f"宠物玩具候选{index}",
            "pic_url": f"https://images.example/candidate-{index}.jpg",
            "source_channel": "image",
            "image_search_rank": index,
            "image_similarity_score": 0.20 - index / 100,
            "image_similarity_selected": True,
            "image_similarity_fallback": True,
        }
        for index in range(1, 6)
    ]
    preview = build_source_preview(
        [
            QuoteItem(
                skc_id="55872375182",
                product_title="Cute Cartoon Dog Vest, Cooling Breathable Pet Clothing",
            )
        ],
        {
            "items": [
                {
                    "task_key": "55872375182",
                    "status": "succeeded",
                    "candidates": raw_candidates,
                }
            ]
        },
    )

    ranked = _apply_batch_ranking(preview, selections_by_skc={}, ranking_mode="image_order")

    assert len(ranked["items"][0]["all_candidates"]) == 5
    assert len(ranked["items"][0]["ranked_candidates"]) == 5
    assert all(
        candidate["source_decision"] == "no_reliable_source"
        for candidate in ranked["items"][0]["all_candidates"]
    )
