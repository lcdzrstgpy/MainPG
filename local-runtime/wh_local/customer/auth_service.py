from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any

from ..db import transaction
from .contracts import CustomerAuthActionResult, CustomerAuthResult


DEFAULT_ITERATIONS = 200_000
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_CODE = "local-demo"
PASSWORD_RESET_TTL = timedelta(minutes=30)
DEFAULT_WORKSPACE_NAME = "本地演示工作区"
# 会话失联阈值：前端心跳间隔约 30 秒，超过该阈值未刷新 last_used_at 视为
# 已关闭页面/断线，允许该账号重新登录并撤销旧会话。
SESSION_STALE_SECONDS = 90


class SQLiteCustomerAuthService:
    """Local SQLite customer-account service.

    This service is the phase-2 replacement for the temporary mock auth server.
    It keeps the external /api/customer/* route contract stable while storing
    real accounts and password credentials in the shared SQLite database.
    """

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def login(self, payload: dict[str, Any]) -> CustomerAuthResult:
        identifier = _text(payload, "username") or _text(payload, "email")
        password = _text(payload, "password")
        if not identifier or not password:
            self._log_login("", identifier, "", False, "missing username/email or password")
            raise ValueError("username/email and password are required")

        with transaction(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT
                    a.account_id,
                    a.username,
                    a.email,
                    a.display_name,
                    a.role,
                    a.workspace_id,
                    a.account_status,
                    w.workspace_code,
                    w.workspace_name,
                    c.password_hash,
                    c.salt,
                    c.algorithm,
                    c.iterations
                FROM auth_accounts a
                JOIN auth_password_credentials c ON c.account_id = a.account_id
                LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
                WHERE lower(a.username) = lower(?)
                   OR (a.email <> '' AND lower(a.email) = lower(?))
                """,
                (identifier, identifier),
            ).fetchone()

            if row is None:
                conn.execute(
                    """
                    INSERT INTO auth_login_logs (username, email, success, failure_reason, created_at)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (identifier, identifier if "@" in identifier else "", "account not found", _utc_now()),
                )
                raise PermissionError("invalid username/email or password")

            if str(row["account_status"]).lower() in {"disabled", "inactive", "locked", "suspended", "deleted"}:
                conn.execute(
                    """
                    INSERT INTO auth_login_logs (account_id, username, email, success, failure_reason, created_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (row["account_id"], row["username"], row["email"], "account is not active", _utc_now()),
                )
                raise PermissionError("customer account is not active")

            if not _verify_password(password, row["salt"], row["password_hash"], int(row["iterations"])):
                conn.execute(
                    """
                    INSERT INTO auth_login_logs (account_id, username, email, success, failure_reason, created_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (row["account_id"], row["username"], row["email"], "bad password", _utc_now()),
                )
                raise PermissionError("invalid username/email or password")

            # 单端登录限制：账号存在"活跃且未失联"的平台会话时，禁止再次登录。
            # 失联判定：前端会周期性心跳刷新 last_used_at；超过阈值未心跳视为
            # 已关闭页面/断线，此时允许重新登录并撤销旧会话，避免用户被锁死。
            now = _utc_now()
            stale_before = _utc_ago(SESSION_STALE_SECONDS)
            active_session = conn.execute(
                """
                SELECT session_id, last_used_at FROM auth_platform_sessions
                WHERE account_id = ?
                  AND revoked_at = ''
                  AND expires_at > ?
                """,
                (row["account_id"], now),
            ).fetchone()
            if active_session is not None:
                last_used = str(active_session["last_used_at"] or "")
                still_active = bool(last_used and last_used >= stale_before)
                if still_active:
                    conn.execute(
                        """
                        INSERT INTO auth_login_logs (account_id, username, email, success, failure_reason, created_at)
                        VALUES (?, ?, ?, 0, ?, ?)
                        """,
                        (row["account_id"], row["username"], row["email"], "账号已在其他设备登录", now),
                    )
                    raise PermissionError("该账号已在其他设备登录，请先退出后再登录")
                # 旧会话已失联（如关闭页面未登出），撤销并允许本次登录。
                conn.execute(
                    """
                    UPDATE auth_platform_sessions
                    SET revoked_at = ?
                    WHERE account_id = ? AND revoked_at = ''
                    """,
                    (now, row["account_id"]),
                )

            conn.execute(
                """
                UPDATE auth_accounts
                SET login_status = 'online', updated_at = ?
                WHERE account_id = ?
                """,
                (now, row["account_id"]),
            )
            conn.execute(
                """
                INSERT INTO auth_login_logs (account_id, username, email, success, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (row["account_id"], row["username"], row["email"], now),
            )

            return CustomerAuthResult(
                customer_id=row["account_id"],
                username=row["username"],
                email=row["email"],
                account_status=row["account_status"],
                login_status="online",
                role=row["role"],
                workspace_code=row["workspace_code"] or DEFAULT_WORKSPACE_CODE,
                workspace_name=row["workspace_name"] or DEFAULT_WORKSPACE_NAME,
                raw={"provider": "sqlite", "account_id": row["account_id"], "login_status": "online"},
            )

    def register(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        username = _text(payload, "username")
        email = _text(payload, "email")
        password = _text(payload, "password")
        invitation_code = _text(payload, "invitation_code")
        if not username:
            raise ValueError("username is required")
        if not password or len(password) < 6:
            raise ValueError("password must be at least 6 characters")
        if not invitation_code:
            raise ValueError("invitation code is required")

        # 初版阶段所有自注册用户统一授予 admin，方便本地工作台直接操作系统配置。
        # 后续如需权限分级，再改回 operator 并通过邀请/分配流程授予角色。
        role = "admin"
        workspace_code = _text(payload, "workspace_code") or DEFAULT_WORKSPACE_CODE
        workspace_name = _text(payload, "workspace_name") or DEFAULT_WORKSPACE_NAME
        account_id = _account_id(username, email)
        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt, DEFAULT_ITERATIONS)
        now = _utc_now()

        try:
            with transaction(self.database_path) as conn:
                # 校验邀请码：存在、未过期、未用尽，通过后 used_count + 1
                row = conn.execute(
                    """
                    SELECT code, max_uses, used_count, expires_at
                    FROM invitation_codes
                    WHERE code = ?
                    """,
                    (invitation_code,),
                ).fetchone()
                if row is None:
                    raise ValueError("invitation code is invalid")
                if _invitation_expired(str(row["expires_at"] or "")):
                    raise ValueError("invitation code has expired")
                if row["used_count"] >= row["max_uses"]:
                    raise ValueError("invitation code has been used up")
                conn.execute(
                    "UPDATE invitation_codes SET used_count = used_count + 1 WHERE code = ?",
                    (invitation_code,),
                )

                workspace_row = conn.execute(
                    "SELECT workspace_id FROM workspaces WHERE workspace_code = ?",
                    (workspace_code,),
                ).fetchone()
                workspace_id = workspace_row["workspace_id"] if workspace_row else workspace_code
                conn.execute(
                    """
                    INSERT INTO workspaces (
                        workspace_id, workspace_code, workspace_name, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(workspace_id) DO UPDATE SET
                        workspace_code = excluded.workspace_code,
                        workspace_name = excluded.workspace_name,
                        updated_at = excluded.updated_at
                    """,
                    (workspace_id, workspace_code, workspace_name, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO auth_accounts (
                        account_id, username, email, display_name, role, workspace_id,
                        account_status, email_verified_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'active', '', ?, ?)
                    """,
                    (account_id, username, email, _text(payload, "display_name") or username, role, workspace_id, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO auth_password_credentials (
                        account_id, password_hash, salt, algorithm, iterations, updated_at
                    )
                    VALUES (?, ?, ?, 'pbkdf2_sha256', ?, ?)
                    """,
                    (account_id, password_hash, salt, DEFAULT_ITERATIONS, now),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO user_roles (
                        account_id, workspace_id, role, assigned_by, assigned_at
                    )
                    VALUES (?, ?, 'admin', 'self_register', ?)
                    """,
                    (account_id, workspace_id, now),
                )
                conn.execute(
                    """
                    INSERT INTO invitation_code_usages (
                        code, account_id, username, email, used_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (invitation_code, account_id, username, email, now),
                )
        except Exception as exc:
            message = str(exc)
            if "auth_accounts" in message or "auth_password_credentials" in message:
                raise ValueError("username or email already exists") from exc
            raise

        return CustomerAuthActionResult(
            ok=True,
            message="account registered",
            raw={
                "account_id": account_id,
                "username": username,
                "email": email,
                "role": role,
                "workspace_code": workspace_code,
            },
        )

    def activate(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        identifier = _text(payload, "account_id") or _text(payload, "username") or _text(payload, "email")
        if not identifier:
            raise ValueError("account_id, username or email is required")
        with transaction(self.database_path) as conn:
            result = conn.execute(
                """
                UPDATE auth_accounts
                SET account_status = 'active', email_verified_at = ?, updated_at = ?
                WHERE account_id = ?
                   OR lower(username) = lower(?)
                   OR (email <> '' AND lower(email) = lower(?))
                """,
                (_utc_now(), _utc_now(), identifier, identifier, identifier),
            )
            if result.rowcount == 0:
                raise ValueError("account not found")
        return CustomerAuthActionResult(ok=True, message="account activated")

    def email_code(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        email = _text(payload, "email")
        if not email:
            raise ValueError("email is required")
        return CustomerAuthActionResult(ok=True, message="local sqlite auth does not send email code", raw={"email": email})

    def change_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        identifier = _text(payload, "account_id") or _text(payload, "username") or _text(payload, "email")
        current_password = _text(payload, "current_password") or _text(payload, "old_password")
        new_password = _text(payload, "new_password") or _text(payload, "password")
        if not identifier:
            raise ValueError("account_id, username or email is required")
        if not current_password:
            raise ValueError("current password is required")
        if not new_password or len(new_password) < 6:
            raise ValueError("new password must be at least 6 characters")

        with transaction(self.database_path) as conn:
            row = _find_account_with_password(conn, identifier)
            if row is None:
                _log_security_event(conn, "", "change_password", False, {"reason": "account not found"})
                raise PermissionError("invalid username/email or password")
            if not _verify_password(current_password, row["salt"], row["password_hash"], int(row["iterations"])):
                _log_security_event(conn, row["account_id"], "change_password", False, {"reason": "bad current password"})
                raise PermissionError("invalid username/email or password")

            _update_password(conn, row["account_id"], new_password)
            _revoke_platform_sessions(conn, row["account_id"])
            _log_security_event(conn, row["account_id"], "change_password", True)

        return CustomerAuthActionResult(ok=True, message="password changed")

    def forgot_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        identifier = _text(payload, "account_id") or _text(payload, "username") or _text(payload, "email")
        if not identifier:
            raise ValueError("account_id, username or email is required")

        now = _utc_now()
        expires_at = (datetime.now(timezone.utc) + PASSWORD_RESET_TTL).isoformat(timespec="seconds")
        reset_token = f"wh_reset_{secrets.token_urlsafe(32)}"
        reset_id = f"reset_{secrets.token_urlsafe(20)}"
        with transaction(self.database_path) as conn:
            row = _find_account(conn, identifier)
            if row is None:
                _log_security_event(conn, "", "forgot_password", False, {"identifier": identifier, "reason": "account not found"})
                return CustomerAuthActionResult(ok=True, message="if the account exists, a reset token has been generated")
            conn.execute(
                """
                INSERT INTO auth_password_reset_tokens (
                    reset_id, account_id, token_hash, expires_at, used_at, created_at, request_ip, user_agent
                )
                VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    reset_id,
                    row["account_id"],
                    _hash_token(reset_token),
                    expires_at,
                    now,
                    _text(payload, "request_ip"),
                    _text(payload, "user_agent"),
                ),
            )
            _log_security_event(conn, row["account_id"], "forgot_password", True, {"reset_id": reset_id})

        return CustomerAuthActionResult(
            ok=True,
            message="password reset token generated",
            raw={"reset_token": reset_token, "expires_at": expires_at},
        )

    def reset_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        reset_token = _text(payload, "reset_token") or _text(payload, "token")
        new_password = _text(payload, "new_password") or _text(payload, "password")
        if not reset_token:
            raise ValueError("reset_token is required")
        if not new_password or len(new_password) < 6:
            raise ValueError("new password must be at least 6 characters")

        now = _utc_now()
        token_hash = _hash_token(reset_token)
        with transaction(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT reset_id, account_id
                FROM auth_password_reset_tokens
                WHERE token_hash = ?
                  AND used_at = ''
                  AND expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                _log_security_event(conn, "", "reset_password", False, {"reason": "invalid or expired reset token"})
                raise PermissionError("invalid or expired reset token")

            _update_password(conn, row["account_id"], new_password)
            conn.execute(
                "UPDATE auth_password_reset_tokens SET used_at = ? WHERE reset_id = ?",
                (now, row["reset_id"]),
            )
            _revoke_platform_sessions(conn, row["account_id"])
            _log_security_event(conn, row["account_id"], "reset_password", True, {"reset_id": row["reset_id"]})

        return CustomerAuthActionResult(ok=True, message="password reset")

    def password_reset(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        if _text(payload, "reset_token") or _text(payload, "token"):
            return self.reset_password(payload)
        identifier = _text(payload, "account_id") or _text(payload, "username") or _text(payload, "email")
        new_password = _text(payload, "new_password") or _text(payload, "password")
        if not identifier:
            raise ValueError("account_id, username or email is required")
        if not new_password or len(new_password) < 6:
            raise ValueError("new password must be at least 6 characters")

        salt = secrets.token_hex(16)
        password_hash = _hash_password(new_password, salt, DEFAULT_ITERATIONS)
        now = _utc_now()
        with transaction(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT account_id
                FROM auth_accounts
                WHERE account_id = ?
                   OR lower(username) = lower(?)
                   OR (email <> '' AND lower(email) = lower(?))
                """,
                (identifier, identifier, identifier),
            ).fetchone()
            if row is None:
                raise ValueError("account not found")
            conn.execute(
                """
                UPDATE auth_password_credentials
                SET password_hash = ?, salt = ?, algorithm = 'pbkdf2_sha256', iterations = ?, updated_at = ?
                WHERE account_id = ?
                """,
                (password_hash, salt, DEFAULT_ITERATIONS, now, row["account_id"]),
            )
            conn.execute(
                "UPDATE auth_accounts SET updated_at = ? WHERE account_id = ?",
                (now, row["account_id"]),
            )
            _revoke_platform_sessions(conn, row["account_id"])
            _log_security_event(conn, row["account_id"], "password_reset_direct", True)
        return CustomerAuthActionResult(ok=True, message="password reset")

    def _log_login(self, account_id: str, username: str, email: str, success: bool, reason: str) -> None:
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO auth_login_logs (account_id, username, email, success, failure_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (account_id, username, email, 1 if success else 0, reason, _utc_now()),
            )


def _hash_password(password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return digest.hex()


def _verify_password(password: str, salt: str, expected_hash: str, iterations: int) -> bool:
    actual = _hash_password(password, salt, iterations)
    return hmac.compare_digest(actual, str(expected_hash or ""))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _find_account(conn: Any, identifier: str) -> Any:
    return conn.execute(
        """
        SELECT account_id, username, email, account_status
        FROM auth_accounts
        WHERE account_id = ?
           OR lower(username) = lower(?)
           OR (email <> '' AND lower(email) = lower(?))
        """,
        (identifier, identifier, identifier),
    ).fetchone()


def _find_account_with_password(conn: Any, identifier: str) -> Any:
    return conn.execute(
        """
        SELECT
            a.account_id,
            a.username,
            a.email,
            a.account_status,
            c.password_hash,
            c.salt,
            c.iterations
        FROM auth_accounts a
        JOIN auth_password_credentials c ON c.account_id = a.account_id
        WHERE a.account_id = ?
           OR lower(a.username) = lower(?)
           OR (a.email <> '' AND lower(a.email) = lower(?))
        """,
        (identifier, identifier, identifier),
    ).fetchone()


def _update_password(conn: Any, account_id: str, password: str) -> None:
    now = _utc_now()
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt, DEFAULT_ITERATIONS)
    conn.execute(
        """
        UPDATE auth_password_credentials
        SET password_hash = ?, salt = ?, algorithm = 'pbkdf2_sha256', iterations = ?, updated_at = ?
        WHERE account_id = ?
        """,
        (password_hash, salt, DEFAULT_ITERATIONS, now, account_id),
    )
    conn.execute(
        "UPDATE auth_accounts SET updated_at = ? WHERE account_id = ?",
        (now, account_id),
    )


def _revoke_platform_sessions(conn: Any, account_id: str) -> None:
    conn.execute(
        """
        UPDATE auth_platform_sessions
        SET revoked_at = ?
        WHERE account_id = ? AND revoked_at = ''
        """,
        (_utc_now(), account_id),
    )


def _log_security_event(
    conn: Any,
    account_id: str,
    event_type: str,
    success: bool,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO auth_security_events (
            event_id, account_id, event_type, success, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"sec_{secrets.token_urlsafe(20)}",
            account_id,
            event_type,
            1 if success else 0,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            _utc_now(),
        ),
    )


def _account_id(username: str, email: str) -> str:
    seed = (email or username).strip().lower()
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"cust_{digest}"


def _normalize_role(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"operator", "viewer", "editor"}:
        return "operator"
    return "admin"


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _invitation_expired(expires_at: str) -> bool:
    """Compare invitation expiry as an instant, regardless of its UTC offset."""
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        # Preserve the old lexical behavior for any legacy non-ISO values.
        return expires_at < _utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) < datetime.now(timezone.utc)


def _utc_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
