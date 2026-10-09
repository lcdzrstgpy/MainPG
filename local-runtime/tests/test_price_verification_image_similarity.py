from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import threading
import time

from PIL import Image, ImageDraw

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.price_verification.sourcing.image_similarity import (
    IMAGE_FEATURE_CACHE_TTL_DAYS,
    IMAGE_SIMILARITY_THRESHOLD,
    similarity_score,
    verify_visual_candidates,
)
from wh_local.price_verification.sourcing.image_feature_cache import ImageFeatureCache
from wh_local.price_verification.sourcing.normalizer import normalize_source_candidate
from wh_local.price_verification.sourcing.service import _verified_preview_candidate
from wh_local.price_verification.quote_normalizer import QuoteItem


def _image_bytes(*, background: str = "white", object_colour: str = "#3c78d8", decoration: bool = False) -> bytes:
    image = Image.new("RGB", (240, 240), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 70, 195, 175), radius=18, fill=object_colour, outline="#1d3557", width=5)
    draw.ellipse((85, 35, 155, 105), fill="#f4a261", outline="#7f5539", width=4)
    if decoration:
        draw.rectangle((0, 0, 240, 20), fill="#efefef")
        draw.rectangle((0, 220, 240, 240), fill="#efefef")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _solid_image_bytes(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), colour).save(output, format="PNG")
    return output.getvalue()


def test_perceptual_similarity_accepts_small_marketplace_decoration() -> None:
    score = similarity_score(_image_bytes(), _image_bytes(decoration=True))

    assert score >= IMAGE_SIMILARITY_THRESHOLD


def test_similarity_threshold_is_fifty_percent() -> None:
    assert IMAGE_SIMILARITY_THRESHOLD == 0.50


def test_candidate_features_are_cached_for_three_days_without_image_bytes_or_url(tmp_path) -> None:
    reference_url = "https://images.example/reference.png?temporary=secret"
    candidate_url = "https://images.example/candidate.png?signature=private"
    images = {
        reference_url: _image_bytes(),
        candidate_url: _image_bytes(decoration=True),
    }
    fetches: list[str] = []

    def fetch(url: str) -> FetchedPublicImage:
        fetches.append(url)
        return FetchedPublicImage(content=images[url], media_type="image/png", final_url=url)

    cache = ImageFeatureCache(
        tmp_path / "features",
        feature_method="local-phash-dhash-color-v1",
        ttl_seconds=IMAGE_FEATURE_CACHE_TTL_DAYS * 24 * 60 * 60,
    )
    candidates = [{"offer_id": "111111", "image": candidate_url}]

    first, first_audit = verify_visual_candidates(
        reference_url, candidates, fetcher=fetch, feature_cache=cache, minimum_results=1
    )
    second, second_audit = verify_visual_candidates(
        reference_url, candidates, fetcher=fetch, feature_cache=cache, minimum_results=1
    )

    assert IMAGE_FEATURE_CACHE_TTL_DAYS == 3
    assert len(first) == len(second) == 1
    assert first[0]["image_similarity_score"] == second[0]["image_similarity_score"]
    assert fetches == [reference_url, candidate_url, reference_url]
    assert first_audit["feature_cache_miss_count"] == 1
    assert second_audit["feature_cache_hit_count"] == 1
    cache_files = list((tmp_path / "features").rglob("*.json"))
    assert len(cache_files) == 1
    stored = cache_files[0].read_text(encoding="utf-8")
    assert candidate_url not in stored
    assert "signature=private" not in stored
    assert images[candidate_url] not in cache_files[0].read_bytes()


def test_visual_verification_reuses_reference_bytes_without_downloading_reference(tmp_path) -> None:
    reference_url = "https://images.example/reference.png"
    candidate_url = "https://images.example/candidate.png"
    reference_content = _image_bytes()
    fetched: list[str] = []

    def fetch(url: str) -> FetchedPublicImage:
        fetched.append(url)
        assert url == candidate_url
        return FetchedPublicImage(
            content=_image_bytes(decoration=True),
            media_type="image/png",
            final_url=url,
        )

    selected, audit = verify_visual_candidates(
        reference_url,
        [{"offer_id": "111111", "image": candidate_url}],
        reference_content=reference_content,
        fetcher=fetch,
        feature_cache=ImageFeatureCache(tmp_path / "features", feature_method="test"),
        minimum_results=1,
    )

    assert len(selected) == 1
    assert fetched == [candidate_url]
    assert audit["reference_available"] is True
    assert audit["reference_reused"] is True


def test_candidate_downloads_are_globally_limited_across_three_skc_verifications(tmp_path) -> None:
    reference_content = _image_bytes()
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    start = threading.Barrier(3, timeout=2)

    def verify_group(group: int) -> None:
        nonlocal active, maximum_active
        candidates = [
            {"offer_id": f"{group}-{index}", "image": f"https://images.example/{group}-{index}.png"}
            for index in range(6)
        ]

        def fetch(url: str) -> FetchedPublicImage:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return FetchedPublicImage(content=_image_bytes(), media_type="image/png", final_url=url)

        start.wait()
        verify_visual_candidates(
            f"https://images.example/reference-{group}.png",
            candidates,
            reference_content=reference_content,
            fetcher=fetch,
            feature_cache=ImageFeatureCache(tmp_path / f"features-{group}", feature_method="test"),
            minimum_results=1,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(verify_group, range(3)))

    assert maximum_active <= 12


def test_candidate_feature_cache_expires_after_three_days(tmp_path) -> None:
    cache = ImageFeatureCache(
        tmp_path / "features",
        feature_method="test-features",
        ttl_seconds=3 * 24 * 60 * 60,
    )
    variants = [{"average_hash": "1"}]

    assert cache.store("https://images.example/item.png", variants, now=100.0)
    assert cache.load("https://images.example/item.png", now=100.0 + 3 * 24 * 60 * 60 - 1) == variants
    assert cache.load("https://images.example/item.png", now=100.0 + 3 * 24 * 60 * 60) is None


def test_perceptual_similarity_rejects_different_product_composition() -> None:
    reference = _image_bytes()
    unrelated = Image.new("RGB", (240, 240), "#111111")
    draw = ImageDraw.Draw(unrelated)
    for offset in range(0, 240, 24):
        draw.line((0, offset, 240, 240 - offset), fill="#f94144", width=9)
    output = BytesIO()
    unrelated.save(output, format="PNG")

    assert similarity_score(reference, output.getvalue()) < IMAGE_SIMILARITY_THRESHOLD


def test_visual_verification_keeps_only_passing_candidate_and_preserves_link_pair() -> None:
    images = {
        "https://images.example/reference.png": _image_bytes(),
        "https://images.example/same.png": _image_bytes(decoration=True),
        "https://images.example/other.png": _solid_image_bytes("black"),
    }

    def fetch(url: str) -> FetchedPublicImage:
        return FetchedPublicImage(content=images[url], media_type="image/png", final_url=url)

    candidates = [
        {
            "offer_id": "111111",
            "source_url": "https://detail.1688.com/offer/111111.html",
            "image": "https://images.example/same.png",
        },
        {
            "offer_id": "222222",
            "source_url": "https://detail.1688.com/offer/222222.html",
            "image": "https://images.example/other.png",
        },
    ]

    verified, audit = verify_visual_candidates(
        "https://images.example/reference.png", candidates, fetcher=fetch, minimum_results=1
    )

    assert [candidate["offer_id"] for candidate in verified] == ["111111"]
    assert verified[0]["source_url"].endswith("/111111.html")
    assert verified[0]["image_similarity_verified"] is True
    assert audit["verified_count"] == 1
    assert audit["rejected_count"] == 1


def test_visual_verification_fills_five_with_best_below_threshold_candidates() -> None:
    reference_url = "https://images.example/reference.png"
    images = {reference_url: _image_bytes()}
    candidates = []
    for index in range(8):
        image_url = f"https://images.example/{index}.png"
        images[image_url] = _solid_image_bytes(f"#{index + 1:02x}{index + 2:02x}{index + 3:02x}")
        candidates.append({
            "offer_id": str(100000 + index),
            "source_url": f"https://detail.1688.com/offer/{100000 + index}.html",
            "image": image_url,
        })

    def fetch(url: str) -> FetchedPublicImage:
        return FetchedPublicImage(content=images[url], media_type="image/png", final_url=url)

    selected, audit = verify_visual_candidates(reference_url, candidates, fetcher=fetch)

    assert len(selected) == 5
    assert audit["fallback_count"] == 5
    assert all(candidate["image_similarity_fallback"] for candidate in selected)
    assert all(candidate["image_similarity_selected"] for candidate in selected)


def test_title_guard_rejects_unrelated_pet_medicine_for_cooling_mat() -> None:
    candidate = normalize_source_candidate(
        QuoteItem(product_title="Pet Cooling Mat Summer Ice Pad for Dogs and Cats"),
        {
            "num_iid": "333333",
            "item_url": "https://detail.1688.com/offer/333333.html",
            "title": "猫狗体外驱虫滴剂宠物除虫药",
        },
    )

    assert candidate["product_evidence_status"] == "conflict"
    assert candidate["product_evidence"] == ["product_category_mismatch"]


def test_verified_candidate_is_resolved_from_saved_offer_image_pair() -> None:
    preview = {
        "items": [
            {
                "skc_id": "skc-1",
                "all_candidates": [
                    {
                        "offer_id": "111111",
                        "source_url": "https://detail.1688.com/offer/111111.html",
                        "main_image_url": "https://images.example/111111.jpg",
                        "image_similarity_verified": True,
                        "image_similarity_selected": True,
                    },
                    {
                        "offer_id": "222222",
                        "source_url": "https://detail.1688.com/offer/222222.html",
                        "main_image_url": "https://images.example/222222.jpg",
                        "image_similarity_verified": False,
                        "image_similarity_selected": False,
                    },
                ],
            }
        ]
    }

    assert _verified_preview_candidate(preview, "skc-1", "111111")["main_image_url"].endswith("111111.jpg")
    assert _verified_preview_candidate(preview, "skc-1", "222222") is None
