from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.quote_normalizer import (  # noqa: E402
    ForbiddenPlatformWriteError,
    assert_read_only_evidence,
    normalize_price_quote_discovery,
    redact_sensitive,
)


def load_fixture(name: str) -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_network_evidence_wins_and_popup_dom_supplies_adjusted_price() -> None:
    preview = normalize_price_quote_discovery(load_fixture("temu_quote_popup_dom.json"))

    assert preview.quotes[0].skc_id == "SKC-1001"
    assert preview.quotes[0].adjusted_declared_price_cny == Decimal("18.90")
    assert preview.quotes[0].original_declared_price_cny == Decimal("20.00")
    assert preview.quotes[0].capture_method == "network_json | batch_price_popup"
    assert preview.counts.complete_quotes == 1


def test_network_fixture_preserves_quote_identity_and_image_evidence() -> None:
    preview = normalize_price_quote_discovery(load_fixture("temu_quote_network.json"))

    quote = preview.quotes[0]
    assert (quote.skc_id, quote.sku_id, quote.site) == (
        "SKC-1001",
        "SKU-2001",
        "美国站",
    )
    assert quote.sku_merchant_code == "BAG-BLK-01"
    assert quote.main_image_url == "https://images.example/1001.jpg"
    assert quote.network_evidence_count == 1
    assert preview.counts.network_records == 1


def test_network_cent_price_fields_are_normalized_to_cny_decimals() -> None:
    payload = load_fixture("temu_quote_network.json")
    item = payload["records"][0]["responseJson"]["data"]["priceReviewItemList"][0]  # type: ignore[index]
    item["supplyPrice"] = 2000
    item["suggestSupplyPrice"] = 1890

    quote = normalize_price_quote_discovery(payload).quotes[0]

    assert quote.original_declared_price_cny == Decimal("20.00")
    assert quote.adjusted_declared_price_cny == Decimal("18.90")


def test_unconfirmed_popup_row_cannot_supply_adjusted_price() -> None:
    payload = load_fixture("temu_quote_popup_dom.json")
    payload["actions"] = {"batch_price_popup": {"ok": False}}
    payload["dom"] = {"dialog_present": False, "rows": payload["dom"]["rows"]}  # type: ignore[index]

    preview = normalize_price_quote_discovery(payload)

    assert preview.quotes[0].adjusted_declared_price_cny is None
    assert preview.counts.dom_rows_ignored_by_popup_state == 1


def test_evidence_is_redacted_and_write_action_payload_is_rejected() -> None:
    assert redact_sensitive({"cookie": "secret", "message": "token=secret"}) == {
        "cookie": "[REDACTED]",
        "message": "token=[REDACTED]",
    }
    with pytest.raises(ForbiddenPlatformWriteError):
        assert_read_only_evidence({"records": [{"url": "/price/accept"}]})
    with pytest.raises(ForbiddenPlatformWriteError):
        normalize_price_quote_discovery({"records": [{"url": "/price/accept"}]})
