"""Deterministic preflight rules for the local Product Processing module.

These checks intentionally do not call an external AI, image, legal, or COS API.
They consume explicit upstream risk tags and operator-provided terms, then return a
transparent result that can be reviewed or replaced by a centrally managed policy
service later.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit


_IP_RISK_MARKERS = (
    "ip",
    "trademark",
    "infringement",
    "copyright",
    "brand_risk",
    "manual_ip_check",
    "侵权",
    "商标",
    "版权",
)
_QUALIFICATION_MARKERS = (
    "qualification",
    "medical",
    "medicine",
    "children",
    "infant",
    "医疗",
    "医用",
    "药品",
    "儿童",
    "婴儿",
    "婴童",
)


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    message: str
    operator_hint: str
    status: str = "failed"


@dataclass(frozen=True)
class ResolvedExternalURL:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def product_policy_issue(
    raw: dict[str, Any],
    *,
    title: str,
    category: str,
    ip_check: bool,
    qualification_enabled: bool,
    extra_infringement_terms: Iterable[str] = (),
) -> PolicyIssue | None:
    """Return one explicit offline policy issue, or ``None`` when preflight passes."""
    source_text = " ".join(
        part
        for part in (
            title,
            category,
            str(raw.get("description") or ""),
            str(raw.get("source_title") or ""),
            str(raw.get("source_category_path") or ""),
        )
        if part
    ).lower()
    risk_tags = [str(value).strip().lower() for value in raw.get("risk_tags") or []]

    if ip_check:
        tagged = _first_contains(risk_tags, _IP_RISK_MARKERS)
        if tagged:
            return PolicyIssue(
                "ip_risk_tagged",
                f"侵权词过滤：每日采集风险标签命中 {tagged}",
                "请人工确认授权或替换商品后再处理。",
            )
        term = _first_contains(source_text, _normalise_terms(extra_infringement_terms))
        if term:
            return PolicyIssue(
                "ip_term_matched",
                f"侵权词过滤：标题或资料命中配置词 {term}",
                "请确认品牌授权；未获授权时应剔除该商品。",
            )

    tagged = _first_contains(risk_tags, _QUALIFICATION_MARKERS)
    category_term = _first_contains(source_text, _QUALIFICATION_MARKERS)
    if (tagged or category_term) and not qualification_enabled:
        matched = tagged or category_term
        return PolicyIssue(
            "qualification_review_required",
            f"资质品预筛查：命中 {matched}",
            "未启用资质品处理。请确认公司资质后开启该选项，或剔除该商品。",
            status="attention_required",
        )
    return None


def strict_external_url_issue(*, source_url: str, image_url: str) -> PolicyIssue | None:
    """Validate supplied remote URLs without fetching them or making network calls."""
    for label, value, required in (
        ("来源链接", source_url, True),
        ("主图链接", image_url, False),
    ):
        normalized = str(value or "").strip()
        if not normalized and not required:
            continue
        if not normalized:
            return PolicyIssue(
                "strict_external_source_missing",
                f"严格外部链路：缺少{label}",
                "补充可公开访问的 HTTPS/HTTP 来源链接后重试。",
                status="attention_required",
            )
        if not is_safe_external_url(normalized):
            return PolicyIssue(
                "strict_external_url_invalid",
                f"严格外部链路：{label}不是允许的公开 HTTP(S) 地址",
                "请使用不含账号、内网地址或 localhost 的公开 HTTPS/HTTP 链接。",
                status="attention_required",
            )
    return None


def resolve_safe_external_url(
    value: str,
    *,
    resolver: Callable[..., list[Any]] | None = None,
) -> ResolvedExternalURL | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    hostname = parsed.hostname.strip().lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        lookup = resolver or socket.getaddrinfo
        try:
            answers = lookup(hostname, port, type=socket.SOCK_STREAM)
        except (OSError, ValueError):
            return None
        addresses = tuple(
            dict.fromkeys(
                str(answer[4][0]).strip()
                for answer in answers
                if len(answer) >= 5 and answer[4] and str(answer[4][0]).strip()
            )
        )
        if not addresses:
            return None
        try:
            parsed_addresses = tuple(ipaddress.ip_address(item) for item in addresses)
        except ValueError:
            return None
        if not all(_is_global_address(item) for item in parsed_addresses):
            return None
        return ResolvedExternalURL(str(value), hostname, port, addresses)
    if not _is_global_address(address):
        return None
    return ResolvedExternalURL(str(value), hostname, port, (str(address),))


def is_safe_external_url(
    value: str,
    *,
    resolver: Callable[..., list[Any]] | None = None,
) -> bool:
    return resolve_safe_external_url(value, resolver=resolver) is not None


def _is_global_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _first_contains(values: Iterable[str], needles: Iterable[str]) -> str:
    normalized_needles = [str(value).strip().lower() for value in needles if str(value).strip()]
    if isinstance(values, str):
        values = (values,)
    for value in values:
        current = str(value).strip().lower()
        if not current:
            continue
        for needle in normalized_needles:
            if needle in current:
                return needle
    return ""


def _normalise_terms(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))
