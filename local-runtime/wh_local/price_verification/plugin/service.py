"""Local-only, credential-safe plugin pairing and command delivery."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from ..contracts import (
    ALLOWED_PLUGIN_COMMAND_TYPES,
    PriceVerificationActor,
    PriceVerificationContractError,
    safe_json_value,
)
from ..repository import (
    PairingCodeConsumed,
    PairingCodeExpired,
    PairingCodeWorkspaceNotFound,
    PluginCommandRecord,
    PluginSessionRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
)


PAIRING_TTL = timedelta(minutes=10)
COMMAND_LEASE = timedelta(seconds=120)
_RESULT_STATUSES = frozenset({"running", "succeeded", "failed"})


class PluginAuthenticationError(PermissionError):
    """Raised when a pairing or session credential cannot be used."""


class PluginLeaseError(ValueError):
    """Raised when a plugin tries to update a command without its live lease."""


class PluginResourceNotFound(LookupError):
    """Raised when a valid plugin credential lacks ownership of a resource."""


@dataclass(frozen=True)
class IssuedPairingCode:
    pairing_id: str
    code: str
    expires_at: str


@dataclass(frozen=True)
class PluginSession:
    session_id: str
    token: str
    workspace_id: str
    browser: str
    status: str


@dataclass(frozen=True)
class PluginSessionSummary:
    """Safe session metadata for the authenticated workspace owner."""

    session_id: str
    workspace_id: str
    browser: str
    plugin_version: str
    capabilities: Mapping[str, Any]
    status: str
    created_at: str
    last_seen_at: str


class PluginBridgeService:
    """Own plugin credentials and use the reviewed repository for all records.

    No network client is accepted or constructed here.  The bridge exchanges
    local credentials for pre-existing, read-only command records only.
    """

    def __init__(
        self,
        *,
        repository: PriceVerificationRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue_pairing_code(self, actor: PriceVerificationActor) -> IssuedPairingCode:
        actor = _actor(actor)
        code = secrets.token_urlsafe(32)
        expires_at = _as_utc(self._clock()) + PAIRING_TTL
        record = self._repository.create_pairing_code(
            workspace_id=actor.workspace_id,
            code_sha256=_sha256(code),
            expires_at=_timestamp(expires_at),
        )
        return IssuedPairingCode(record.pairing_id, code, record.expires_at)

    def connect(
        self,
        pairing_code: str,
        *,
        browser_name: str,
        capabilities: Mapping[str, Any],
        plugin_version: str = "",
        actor: PriceVerificationActor | None = None,
    ) -> PluginSession:
        pairing_code = _required_text(pairing_code, "pairing_code")
        browser_name = _required_text(browser_name, "browser_name")
        safe_capabilities = _safe_mapping(capabilities, "capabilities")
        token = secrets.token_urlsafe(48)
        now = _timestamp(_as_utc(self._clock()))
        code_hash = _sha256(pairing_code)

        actor = _actor(actor) if actor is not None else None
        try:
            record = self._repository.connect_plugin_session(
                code_sha256=code_hash,
                session_token_hash=_sha256(token),
                browser=browser_name,
                capabilities=safe_capabilities,
                plugin_version=str(plugin_version),
                now=now,
                expected_workspace_id=actor.workspace_id if actor is not None else None,
            )
        except PairingCodeWorkspaceNotFound as error:
            raise PluginResourceNotFound("pairing code not found") from error
        except (PriceVerificationNotFound, PairingCodeConsumed) as error:
            raise PluginAuthenticationError(str(error)) from error
        except PairingCodeExpired as error:
            raise PluginAuthenticationError("pairing code has expired") from error
        return PluginSession(
            session_id=record.session_id,
            token=token,
            workspace_id=record.workspace_id,
            browser=record.browser,
            status=record.status,
        )

    def poll(self, session_token: str, *, limit: int = 10) -> tuple[PluginCommandRecord, ...]:
        session = self._session_for_token(session_token)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise PriceVerificationContractError("limit must be an integer")
        limit = max(1, min(limit, 50))
        now = _as_utc(self._clock())
        now_text = _timestamp(now)
        lease_expires_at = _timestamp(now + COMMAND_LEASE)
        return self._repository.lease_plugin_commands(
            workspace_id=session.workspace_id,
            session_id=session.session_id,
            command_types=tuple(ALLOWED_PLUGIN_COMMAND_TYPES),
            now=now_text,
            lease_expires_at=lease_expires_at,
            limit=limit,
        )

    def receive_result(
        self,
        session_token: str,
        command_id: str,
        status: str,
        result: Mapping[str, Any],
    ) -> PluginCommandRecord:
        safe_result = _safe_mapping(result, "result")
        session = self._session_for_token(session_token)
        command_id = _required_text(command_id, "command_id")
        if status not in _RESULT_STATUSES:
            raise PriceVerificationContractError("unsupported command status")
        now = _as_utc(self._clock())
        now_text = _timestamp(now)
        lease_expires_at = _timestamp(now + COMMAND_LEASE)

        try:
            return self._repository.record_plugin_result(
                workspace_id=session.workspace_id,
                session_id=session.session_id,
                command_id=command_id,
                status=status,
                result=safe_result,
                now=now_text,
                lease_expires_at=lease_expires_at if status == "running" else None,
            )
        except PriceVerificationNotFound as error:
            raise PluginResourceNotFound("plugin command not found") from error
        except ValueError as error:
            raise PluginLeaseError(str(error)) from error

    def list_sessions(self, actor: PriceVerificationActor) -> tuple[PluginSessionSummary, ...]:
        actor = _actor(actor)
        rows = self._repository.list_plugin_sessions(workspace_id=actor.workspace_id)
        return tuple(
            PluginSessionSummary(
                session_id=row.session_id,
                workspace_id=row.workspace_id,
                browser=row.browser,
                plugin_version=row.plugin_version,
                capabilities=row.capabilities,
                status=row.status,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
            )
            for row in rows
        )

    def _session_for_token(self, session_token: str) -> PluginSessionRecord:
        try:
            return self._repository.get_plugin_session_by_token_hash(
                session_token_hash=_sha256(_required_text(session_token, "session_token"))
            )
        except (PriceVerificationNotFound, ValueError) as error:
            raise PluginAuthenticationError("invalid plugin session") from error


def _actor(value: PriceVerificationActor) -> PriceVerificationActor:
    if not isinstance(value, PriceVerificationActor):
        raise TypeError("actor must be PriceVerificationActor")
    return value


def _safe_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PriceVerificationContractError(f"{name} must be a mapping")
    safe = safe_json_value(value)
    if not isinstance(safe, Mapping):
        raise PriceVerificationContractError(f"{name} must be a mapping")
    return safe


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginAuthenticationError(f"{name} is required")
    return value.strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds")
