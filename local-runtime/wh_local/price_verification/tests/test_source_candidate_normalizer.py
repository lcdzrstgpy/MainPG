from wh_local.price_verification.quote_normalizer import QuoteItem
from wh_local.price_verification.sourcing.normalizer import normalize_source_candidate


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
