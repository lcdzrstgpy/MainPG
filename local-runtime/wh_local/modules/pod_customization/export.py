from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from .dianxiaomi import DXM_COLUMNS, build_dianxiaomi_workbook
from .title_runtime import validate_listing_copy_text


LISTING_IMAGE_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")
LISTING_PRESENTATION_ROLES = ("lifestyle", "detail_a", "detail_b", "hero")
SETTLED_BATCH_STATUSES = frozenset({"completed", "partial_failure", "failed", "cancelled"})
# 账务任务与生成结果独立；已生成的完整款式可正常导出，未结算账务由其自身恢复操作处理。
BILLING_INTERRUPTED_BATCH_STATUSES = frozenset({"billing_auth_required", "settlement_pending"})
EXPORT_CANDIDATE_BATCH_STATUSES = SETTLED_BATCH_STATUSES | BILLING_INTERRUPTED_BATCH_STATUSES
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRIVATE_DNS_SUFFIXES = (
    "arpa",
    "internal",
    "intranet",
    "invalid",
    "lan",
    "local",
    "localdomain",
    "localhost",
    "onion",
    "test",
)
_DYNAMIC_ADDRESS_DNS_SUFFIXES = (
    "localtest.me",
    "localhost.direct",
    "lvh.me",
    "nip.io",
    "sslip.io",
    "xip.io",
)
_DXM_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class DianxiaomiExport:
    content: bytes
    exported_style_count: int
    skipped_style_count: int
    filename: str
    export_id: str = ""


@dataclass(frozen=True)
class ExportAnalysis:
    exportable_styles: dict[int, dict[str, str]]
    skipped_style_count: int
    block_reason: str | None

    @property
    def ready(self) -> bool:
        return self.block_reason is None


def analyze_dianxiaomi_export(
    batch: dict[str, Any], style_copies: dict[int, Any]
) -> ExportAnalysis:
    requested_count = int(batch["requested_count"])
    status = batch["status"]
    if status not in EXPORT_CANDIDATE_BATCH_STATUSES:
        return ExportAnalysis({}, requested_count, "active_batch")
    if not batch.get("listing_fields"):
        return ExportAnalysis({}, requested_count, "listing_fields_missing")

    exportable: dict[int, dict[str, str]] = {}
    structurally_complete: set[int] = set()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in batch.get("items", []):
        grouped.setdefault(int(item["style_index"]), []).append(item)
    for style_index in range(1, requested_count + 1):
        images = _style_images(grouped.get(style_index, []))
        if images is None:
            continue
        structurally_complete.add(style_index)
        if _is_complete_style_copy(style_copies.get(style_index)):
            exportable[style_index] = images

    if exportable:
        return ExportAnalysis(exportable, requested_count - len(exportable), None)
    if structurally_complete:
        return ExportAnalysis({}, requested_count, "style_copy_missing")
    return ExportAnalysis({}, requested_count, "no_exportable_styles")


def build_pod_dianxiaomi_export(
    batch: dict[str, Any], style_copies: dict[int, Any]
) -> DianxiaomiExport:
    analysis = analyze_dianxiaomi_export(batch, style_copies)
    if analysis.block_reason is not None:
        raise ValueError(analysis.block_reason)
    skus = _export_skus(batch["listing_fields"])
    rows = [
        _build_row(
            style_index,
            analysis.exportable_styles[style_index],
            style_copies[style_index],
            batch["business_fields"],
            batch["listing_fields"],
            sku,
            sku_index=sku_index,
        )
        for style_index in sorted(analysis.exportable_styles)
        for sku_index, sku in enumerate(skus, start=1)
    ]
    return DianxiaomiExport(
        content=build_dianxiaomi_workbook(rows),
        exported_style_count=len(analysis.exportable_styles),
        skipped_style_count=analysis.skipped_style_count,
        filename=f'pod_dxm_{batch["batch_id"][:8]}.xlsx',
    )


def _style_images(items: list[dict[str, Any]]) -> dict[str, str] | None:
    if len(items) != 4 or any(item.get("status") != "completed" for item in items):
        return None
    by_role: dict[str, str] = {}
    for item in items:
        role = str(item.get("role") or "")
        public_url = str(item.get("public_url") or "").strip()
        if role not in LISTING_IMAGE_ROLES or role in by_role or not _is_public_https_url(public_url):
            return None
        by_role[role] = public_url
    if set(by_role) != set(LISTING_IMAGE_ROLES):
        return None
    return by_role


def _is_public_https_url(value: str) -> bool:
    decoded = unquote(value)
    if "\\" in value or any(
        _is_unsafe_attribute_character(character) for character in value + decoded
    ):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    hostname = _canonical_hostname(parsed.hostname)
    if hostname is None:
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(hostname))
        except OSError:
            return _is_public_dns_hostname(hostname)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _canonical_hostname(hostname: str) -> str | None:
    try:
        canonical = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if not canonical or len(canonical) > 253:
        return None
    return canonical


def _is_public_dns_hostname(hostname: str) -> bool:
    labels = hostname.split(".")
    if len(labels) < 2 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        return False
    if any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in _PRIVATE_DNS_SUFFIXES):
        return False
    if any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in _DYNAMIC_ADDRESS_DNS_SUFFIXES
    ):
        return False
    return not _contains_private_dotted_address(labels)


def _contains_private_dotted_address(labels: list[str]) -> bool:
    for start in range(max(0, len(labels) - 3)):
        candidate = ".".join(labels[start : start + 4])
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return True
    return False


def _is_complete_style_copy(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(field), str) and bool(value[field].strip())
        for field in ("title", "english_title", "description")
    )


def _raw_listing_copy_text(field: str, value: object) -> str:
    """Write a manual listing copy verbatim, without re-applying AI copy rules."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _export_skus(listing_fields: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Expand current SKU snapshots and retain global fields for legacy snapshots."""
    saved_skus = listing_fields.get("skus")
    if isinstance(saved_skus, list):
        return tuple(sku for sku in saved_skus if isinstance(sku, dict))

    saved_names = listing_fields.get("sku_names")
    names = (
        tuple(name.strip() for name in saved_names if isinstance(name, str) and name.strip())
        if isinstance(saved_names, list)
        else ()
    )
    return tuple(
        {
            "name": name,
            "length_cm": listing_fields["length_cm"],
            "width_cm": listing_fields["width_cm"],
            "height_cm": listing_fields["height_cm"],
            "weight_g": listing_fields["weight_g"],
        }
        for name in (names or ("",))
    )


def _is_unsafe_attribute_character(value: str) -> bool:
    return (
        value in {'"', "'", "<", ">"}
        or value.isspace()
        or unicodedata.category(value).startswith("C")
    )


def _build_row(
    style_index: int,
    images: dict[str, str],
    copy: dict[str, str],
    business_fields: dict[str, Any],
    listing_fields: dict[str, Any],
    sku: dict[str, Any] | None = None,
    *,
    sku_index: int = 1,
) -> list[Any]:
    manual = copy.get("source") == "manual"
    if manual:
        safe_title = _raw_listing_copy_text("title", copy.get("title"))
        safe_english_title = _raw_listing_copy_text("english_title", copy.get("english_title"))
        safe_description = _raw_listing_copy_text("description", copy.get("description"))
    else:
        safe_title = validate_listing_copy_text("title", copy.get("title"), max_length=200)
        safe_english_title = validate_listing_copy_text(
            "english_title", copy.get("english_title"), max_length=200
        )
        safe_description = validate_listing_copy_text(
            "description", copy.get("description"), max_length=1000
        )
    suffix = f"{style_index:03d}"
    image_urls = [images[role] for role in LISTING_PRESENTATION_ROLES]
    description = "\n".join(
        [safe_description, *(f'<img src="{url}" />' for url in image_urls)]
    )
    selected_title = (
        safe_english_title if listing_fields.get("title_mode") == "short" else safe_title
    )
    category = listing_fields.get("category_name") or business_fields["product_category"]
    sku = sku or listing_fields
    product_code = _product_code(listing_fields, style_index)
    sku_code = _sku_code(sku, style_index, sku_index)
    row: list[Any] = ["" for _ in DXM_COLUMNS]
    values = {
        0: selected_title,
        1: selected_title,
        2: description,
        3: product_code,
        4: "尺寸",
        5: sku["name"],
        8: images["lifestyle"],
        9: listing_fields["declared_price"],
        10: sku_code,
        11: sku["length_cm"],
        12: sku["width_cm"],
        13: sku["height_cm"],
        14: sku["weight_g"],
        18: "\n".join(image_urls),
        19: images["hero"],
        23: listing_fields["suggested_price_usd"],
        26: category,
        27: category,
        28: category,
        30: "单品",
        31: 1,
        32: "件",
    }
    for index, value in values.items():
        row[index] = value
    if len(row) != 42:
        raise AssertionError("POD Dianxiaomi row must contain exactly 42 cells")
    return row


def _safe_dxm_code(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _DXM_CODE.fullmatch(candidate) else ""


def _product_code(listing_fields: dict[str, Any], style_index: int) -> str:
    suffix = f"{style_index:03d}"
    prefix = _safe_dxm_code(listing_fields.get("product_code_prefix")) or "POD"
    return f"{prefix}-{suffix}"


def _sku_code(sku: dict[str, Any], style_index: int, sku_index: int) -> str:
    return _safe_dxm_code(sku.get("name")) or f"SKU-{style_index:03d}-{sku_index:02d}"
