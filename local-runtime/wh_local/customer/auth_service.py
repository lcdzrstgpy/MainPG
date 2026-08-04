from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any

from ..db import transaction
from .contracts import CustomerAuthActionResult, CustomerAuthResult


DEFAULT_ITERATIONS = 200_000
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_CODE = "local-demo"
DEFAULT_WORKSPACE_NAME = "本地演示工作区"


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

            conn.execute(
                """
                INSERT INTO auth_login_logs (account_id, username, email, success, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (row["account_id"], row["username"], row["email"], _utc_now()),
            )

            return CustomerAuthResult(
                customer_id=row["account_id"],
                username=row["username"],
                email=row["email"],
                account_status=row["account_status"],
                role=row["role"],
                workspace_code=row["workspace_code"] or DEFAULT_WORKSPACE_CODE,
                workspace_name=row["workspace_name"] or DEFAULT_WORKSPACE_NAME,
                raw={"provider": "sqlite", "account_id": row["account_id"]},
            )

    def register(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        username = _text(payload, "username")
        email = _text(payload, "email")
        password = _text(payload, "password")
        if not username:
            raise ValueError("username is required")
        if not password or len(password) < 6:
            raise ValueError("password must be at least 6 characters")

        role = _normalize_role(_text(payload, "role"))
        workspace_code = _text(payload, "workspace_code") or DEFAULT_WORKSPACE_CODE
        workspace_name = _text(payload, "workspace_name") or DEFAULT_WORKSPACE_NAME
        account_id = _account_id(username, email)
        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt, DEFAULT_ITERATIONS)
        now = _utc_now()

        try:
            with transaction(self.database_path) as conn:
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

    def password_reset(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
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


def _account_id(username: str, email: str) -> str:
    seed = (email or username).strip().lower()
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"cust_{digest}"


def _normalize_role(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"admin", "administrator", "owner", "super_admin"}:
        return "admin"
    return "operator"


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
