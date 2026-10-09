from __future__ import annotations

import ssl
from types import SimpleNamespace

from wh_local.data_collection import public_image_fetch


def test_pinned_https_connection_uses_certifi_ca_bundle(monkeypatch) -> None:
    calls: list[str | None] = []
    # Python 3.11 的 http.client.HTTPSConnection.__init__ 会读取 context.verify_mode，
    # 不能再用裸 object() 充当 fake context。
    fake_context = SimpleNamespace(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)

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
