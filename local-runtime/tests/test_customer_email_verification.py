from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from wh_local.customer.auth_service import SQLiteCustomerAuthService
from wh_local.customer.contracts import CustomerAuthUnavailable
from wh_local.customer.email_sender import EmailDeliveryError
from wh_local.db import init_db, transaction


@dataclass
class RecordingSender:
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send_verification_code(self, recipient: str, code: str) -> None:
        self.sent.append((recipient, code))


class FailingSender:
    def send_verification_code(self, recipient: str, code: str) -> None:
        raise EmailDeliveryError("provider unavailable")


def _service(database_path: Path, sender=None) -> SQLiteCustomerAuthService:
    init_db(database_path)
    return SQLiteCustomerAuthService(
        database_path,
        email_sender=sender or RecordingSender(),
        email_code_secret="test-email-code-secret-that-is-long-enough",
    )


def _seed_invitation(database_path: Path, code: str = "INVITE-TEST") -> None:
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO invitation_codes (code, max_uses, used_count, created_by)
            VALUES (?, 10, 0, 'test')
            """,
            (code,),
        )


def test_send_and_consume_registration_email_code(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    sender = RecordingSender()
    service = _service(database_path, sender)
    _seed_invitation(database_path)

    result = service.email_code({"email": " User@Example.COM ", "purpose": "register"})

    assert result.ok is True
    assert len(sender.sent) == 1
    recipient, code = sender.sent[0]
    assert recipient == "user@example.com"
    assert len(code) == 6 and code.isdigit()

    with transaction(database_path) as conn:
        verification = conn.execute(
            "SELECT email, token_hash, used_at FROM auth_email_verifications"
        ).fetchone()
    assert verification["email"] == "user@example.com"
    assert verification["token_hash"] != code
    assert verification["used_at"] == ""

    registered = service.register(
        {
            "username": "new-user",
            "email": "USER@example.com",
            "email_code": code,
            "password": "strong-password",
            "invitation_code": "INVITE-TEST",
            "workspace_code": "default",
        }
    )

    assert registered.ok is True
    with transaction(database_path) as conn:
        account = conn.execute(
            "SELECT email, email_verified_at FROM auth_accounts WHERE username = 'new-user'"
        ).fetchone()
        consumed = conn.execute(
            "SELECT used_at FROM auth_email_verifications"
        ).fetchone()
    assert account["email"] == "user@example.com"
    assert account["email_verified_at"]
    assert consumed["used_at"]


def test_invalid_code_increments_attempts_without_using_invitation(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    sender = RecordingSender()
    service = _service(database_path, sender)
    _seed_invitation(database_path)
    service.email_code({"email": "user@example.com"})

    with pytest.raises(PermissionError, match="invalid or expired"):
        service.register(
            {
                "username": "new-user",
                "email": "user@example.com",
                "email_code": "000000",
                "password": "strong-password",
                "invitation_code": "INVITE-TEST",
            }
        )

    with transaction(database_path) as conn:
        verification = conn.execute(
            "SELECT attempts, used_at FROM auth_email_verifications"
        ).fetchone()
        invitation = conn.execute(
            "SELECT used_count FROM invitation_codes WHERE code = 'INVITE-TEST'"
        ).fetchone()
    assert verification["attempts"] == 1
    assert verification["used_at"] == ""
    assert invitation["used_count"] == 0


def test_email_code_has_resend_cooldown(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    sender = RecordingSender()
    service = _service(database_path, sender)
    service.email_code({"email": "user@example.com"})

    with pytest.raises(ValueError, match="wait 60 seconds"):
        service.email_code({"email": "user@example.com"})

    assert len(sender.sent) == 1


def test_failed_delivery_removes_unusable_code(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    service = _service(database_path, FailingSender())

    with pytest.raises(CustomerAuthUnavailable, match="could not be sent"):
        service.email_code({"email": "user@example.com"})

    with transaction(database_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM auth_email_verifications").fetchone()["total"]
    assert count == 0


def test_missing_provider_configuration_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    init_db(database_path)
    service = SQLiteCustomerAuthService(database_path)

    with pytest.raises(CustomerAuthUnavailable, match="not configured"):
        service.email_code({"email": "user@example.com"})


def test_forgot_password_sends_code_and_reset_consumes_it(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    sender = RecordingSender()
    service = _service(database_path, sender)
    _seed_invitation(database_path)

    # 注册一个已验证账号（携带 register 验证码）
    service.email_code({"email": "user@example.com", "purpose": "register"})
    register_code = sender.sent[-1][1]
    service.register(
        {
            "username": "new-user",
            "email": "user@example.com",
            "email_code": register_code,
            "password": "strong-password",
            "invitation_code": "INVITE-TEST",
            "workspace_code": "default",
        }
    )

    sender.sent.clear()
    result = service.forgot_password({"email": "user@example.com"})
    assert result.ok is True
    assert not (result.raw or {}).get("reset_token")  # 不再返回 reset_token

    assert len(sender.sent) == 1
    recipient, reset_code = sender.sent[0]
    assert recipient == "user@example.com"
    assert len(reset_code) == 6 and reset_code.isdigit()

    # 错误验证码：失败且不改密码
    with pytest.raises(PermissionError, match="invalid or expired"):
        service.reset_password({"email": "user@example.com", "code": "000000", "new_password": "new-pass-123"})

    # 正确验证码：改密成功
    reset = service.reset_password(
        {"email": "user@example.com", "code": reset_code, "new_password": "new-pass-123"}
    )
    assert reset.ok is True
    assert service.login({"email": "user@example.com", "password": "new-pass-123"}).customer_id

    # 验证码只能使用一次
    with pytest.raises(PermissionError, match="invalid or expired"):
        service.reset_password({"email": "user@example.com", "code": reset_code, "new_password": "another-pass"})


def test_forgot_password_unknown_email_is_silent(tmp_path: Path) -> None:
    database_path = tmp_path / "auth.sqlite3"
    sender = RecordingSender()
    service = _service(database_path, sender)

    result = service.forgot_password({"email": "nobody@example.com"})
    assert result.ok is True
    assert sender.sent == []
