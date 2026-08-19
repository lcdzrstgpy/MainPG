from __future__ import annotations

import socket

import pytest

from wh_local.modules.product_processing.domain.policy import is_safe_external_url


def _answers(*addresses: str):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.4",),
        ("8.8.8.8", "127.0.0.1"),
        ("2606:4700:4700::1111", "fe80::1"),
    ],
)
def test_url_policy_rejects_any_non_global_dns_answer(addresses: tuple[str, ...]) -> None:
    assert not is_safe_external_url(
        "https://images.example.test/result.png",
        resolver=lambda *_args, **_kwargs: _answers(*addresses),
    )


def test_url_policy_accepts_only_when_every_dns_answer_is_global() -> None:
    assert is_safe_external_url(
        "https://images.example.test/result.png",
        resolver=lambda *_args, **_kwargs: _answers("8.8.8.8", "2606:4700:4700::1111"),
    )


def test_url_policy_fails_closed_when_dns_resolution_fails() -> None:
    def fail(*_args, **_kwargs):
        raise socket.gaierror("resolver unavailable")

    assert not is_safe_external_url(
        "https://images.example.test/result.png",
        resolver=fail,
    )
