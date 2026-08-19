from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wh_local.customer import image_credentials


def test_image_credentials_are_decrypted_for_one_context_and_then_cleared(
    monkeypatch,
) -> None:
    session_key = b"s" * 32
    captured: dict[str, object] = {}

    class PublicKey:
        def encrypt(self, value, _padding):
            assert value == session_key
            return b"encrypted-session-key"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            nonce = b"n" * 12
            encryptor = Cipher(algorithms.AES(session_key), modes.GCM(nonce)).encryptor()
            plaintext = json.dumps(
                {
                    "api_key": "temporary-image-key",
                    "base_url": "https://api.wuyinkeji.com",
                }
            ).encode("utf-8")
            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            return json.dumps(
                {
                    "ok": True,
                    "payload": base64.b64encode(ciphertext).decode("ascii"),
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                    "tag": base64.b64encode(encryptor.tag).decode("ascii"),
                }
            ).encode("utf-8")

    def open_request(request, **_kwargs):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(image_credentials.os, "urandom", lambda _size: session_key)
    monkeypatch.setattr(
        image_credentials.serialization,
        "load_pem_public_key",
        lambda _pem: PublicKey(),
    )
    monkeypatch.setattr(image_credentials, "urlopen", open_request)

    leased = None
    with image_credentials.lease_image_credentials(
        base_url="https://platform.example/auth-api",
        remote_token="remote-token",
        usage_id="usage-image",
    ) as credentials:
        leased = credentials
        assert credentials == {
            "api_key": "temporary-image-key",
            "base_url": "https://api.wuyinkeji.com",
        }

    assert leased == {}
    assert captured["url"] == "https://platform.example/auth-api/api/customer/ai/image-credentials"
    assert captured["authorization"] == "Bearer remote-token"
    assert captured["payload"]["usage_id"] == "usage-image"
    assert captured["payload"]["encrypted_session_key"] == base64.b64encode(
        b"encrypted-session-key"
    ).decode("ascii")
