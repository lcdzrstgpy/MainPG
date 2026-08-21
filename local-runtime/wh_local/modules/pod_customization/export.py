from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from .dianxiaomi import DXM_COLUMNS, build_dianxiaomi_workbook


LISTING_IMAGE_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")
SETTLED_BATCH_STATUSES = frozenset({"completed", "partial_failure", "failed"})
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


@dataclass(frozen=True)
class DianxiaomiExport:
    content: bytes
    exported_style_count: int
    skipped_style_count: int
    filename: str


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
    if batch["status"] not in SETTLED_BATCH_STATUSES:
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
    rows = [
        _build_row(
            style_index,
            analysis.exportable_styles[style_index],
            style_copies[style_index],
            batch["business_fields"],
            batch["listing_fields"],
        )
        for style_index in sorted(analysis.exportable_styles)
    ]
    return DianxiaomiExport(
        content=build_dianxiaomi_workbook(rows),
        exported_style_count=len(rows),
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
) -> list[Any]:
    suffix = f"{style_index:03d}"
    image_urls = [images[role] for role in LISTING_IMAGE_ROLES]
    description = "\n".join(
        [copy["description"], *(f'<img src="{url}" />' for url in image_urls)]
    )
    selected_title = (
        copy["english_title"] if listing_fields.get("title_mode") == "short" else copy["title"]
    )
    category = business_fields["product_category"]
    row: list[Any] = ["" for _ in DXM_COLUMNS]
    values = {
        0: selected_title,
        1: selected_title,
        2: description,
        3: f'{listing_fields["product_code_prefix"]}-{suffix}',
        4: "Style",
        5: f"Style {suffix}",
        8: images["hero"],
        9: listing_fields["declared_price"],
        10: f'{listing_fields["sku_prefix"]}-{suffix}',
        11: listing_fields["length_cm"],
        12: listing_fields["width_cm"],
        13: listing_fields["height_cm"],
        14: listing_fields["weight_g"],
        18: "\n".join(image_urls),
        19: images["hero"],
        23: listing_fields["suggested_price_usd"],
        26: category,
        27: category,
        28: category,
        29: listing_fields["category_id"],
        30: "单品",
        31: 1,
        32: "件",
    }
    for index, value in values.items():
        row[index] = value
    if len(row) != 42:
        raise AssertionError("POD Dianxiaomi row must contain exactly 42 cells")
    return row
