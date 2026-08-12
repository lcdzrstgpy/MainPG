from __future__ import annotations

from wh_local.data_collection import public_image_fetch


def test_pinned_https_connection_uses_certifi_ca_bundle(monkeypatch) -> None:
    calls: list[str | None] = []
    fake_context = object()

    monkeypatch.setattr(public_image_fetch.certifi, "where", lambda: "trusted-ca.pem")

    def create_default_context(*, cafile: str | None = None):
        calls.append(cafile)
        return fake_context

    monkeypatch.setattr(public_image_fetch.ssl, "create_default_context", create_default_context)

    connection = public_image_fetch._PinnedHTTPSConnection(
        "cloudflare-dns.com",
        443,
        connect_ip="1.1.1.1",
        timeout=10.0,
    )

    assert calls == ["trusted-ca.pem"]
    assert connection._context is fake_context
