"""Encrypted, server-only storage for upstream AI credentials.

The web admin may list metadata for keys but never returns their plaintext.
Keys are encrypted with a Fernet master key stored in a separate root-only file.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialVaultError(RuntimeError):
    """Raised when the local credential vault cannot safely be used."""


_LOCK = threading.RLock()
_KINDS = {"text", "image"}


def _vault_path() -> Path:
    return Path(
        os.environ.get(
            "WH_CREDENTIAL_VAULT_PATH",
            "/etc/wh-workbench/ai-credentials.v1.json",
        )
    )


def _key_path() -> Path:
    return Path(
        os.environ.get(
            "WH_CREDENTIAL_VAULT_KEY_PATH",
            "/etc/wh-workbench/ai-credentials.key",
        )
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _secure_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(temporary, "xb") as handle:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _fernet() -> Fernet:
    path = _key_path()
    if path.exists():
        key = path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _secure_write(path, key + b"\n")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialVaultError("credential vault master key is invalid") from exc


def _load() -> dict[str, Any]:
    path = _vault_path()
    if not path.exists():
        return {"version": 1, "credentials": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialVaultError("credential vault cannot be read") from exc
    if not isinstance(value, dict) or not isinstance(value.get("credentials"), list):
        raise CredentialVaultError("credential vault format is invalid")
    return value


def _save(document: dict[str, Any]) -> None:
    _secure_write(
        _vault_path(),
        (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
    )


def _validate_kind(kind: str) -> str:
    clean = str(kind or "").strip().lower()
    if clean not in _KINDS:
        raise CredentialVaultError("credential kind is invalid")
    return clean


def _mask(secret: str) -> str:
    if len(secret) < 10:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * max(8, len(secret) - 8)}{secret[-4:]}"


def _public(record: dict[str, Any]) -> dict[str, Any]:
    encrypted = str(record.get("ciphertext") or "")
    return {
        "credential_id": str(record.get("credential_id") or ""),
        "kind": str(record.get("kind") or ""),
        "label": str(record.get("label") or ""),
        "provider": str(record.get("provider") or ""),
        "model": str(record.get("model") or ""),
        "enabled": bool(record.get("enabled")),
        "active": bool(record.get("active")),
        "masked_secret": _mask(_fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")) if encrypted else "",
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }


def list_credentials() -> list[dict[str, Any]]:
    with _LOCK:
        document = _load()
        try:
            return [_public(item) for item in document["credentials"] if isinstance(item, dict)]
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise CredentialVaultError("credential vault ciphertext is invalid") from exc


def import_legacy_credential(kind: str, secret: str, *, label: str) -> dict[str, Any] | None:
    """One-time import of an existing environment credential into the vault.

    The caller is responsible for removing the legacy plaintext after this
    function succeeds.  Existing managed keys are never overwritten.
    """
    clean_kind = _validate_kind(kind)
    clean_secret = str(secret or "").strip()
    if not clean_secret:
        return None
    with _LOCK:
        document = _load()
        if any(
            isinstance(item, dict) and item.get("kind") == clean_kind
            for item in document["credentials"]
        ):
            return None
    return add_credential(clean_kind, label, clean_secret, activate=True)


def add_credential(kind: str, label: str, secret: str, *, activate: bool = True) -> dict[str, Any]:
    clean_kind = _validate_kind(kind)
    clean_label = str(label or "").strip()[:80] or ("文本服务密钥" if clean_kind == "text" else "图片服务密钥")
    clean_secret = str(secret or "").strip()
    if not 16 <= len(clean_secret) <= 512:
        raise CredentialVaultError("credential secret length is invalid")
    with _LOCK:
        document = _load()
        if activate:
            for item in document["credentials"]:
                if isinstance(item, dict) and item.get("kind") == clean_kind:
                    item["active"] = False
                    item["updated_at"] = _timestamp()
        now = _timestamp()
        record = {
            "credential_id": f"cred_{secrets.token_urlsafe(18)}",
            "kind": clean_kind,
            "label": clean_label,
            "provider": "aicoming" if clean_kind == "text" else "wuyin",
            "model": "gpt-5.6-terra" if clean_kind == "text" else "image_gpt",
            "enabled": True,
            "active": bool(activate),
            "ciphertext": _fernet().encrypt(clean_secret.encode("utf-8")).decode("ascii"),
            "created_at": now,
            "updated_at": now,
        }
        document["credentials"].append(record)
        _save(document)
        return _public(record)


def update_credential(credential_id: str, *, label: str | None = None, secret: str | None = None) -> dict[str, Any]:
    with _LOCK:
        document = _load()
        record = next((item for item in document["credentials"] if item.get("credential_id") == credential_id), None)
        if not isinstance(record, dict):
            raise CredentialVaultError("credential not found")
        if label is not None:
            record["label"] = str(label).strip()[:80] or str(record.get("label") or "")
        if secret is not None and str(secret).strip():
            clean_secret = str(secret).strip()
            if not 16 <= len(clean_secret) <= 512:
                raise CredentialVaultError("credential secret length is invalid")
            record["ciphertext"] = _fernet().encrypt(clean_secret.encode("utf-8")).decode("ascii")
        record["updated_at"] = _timestamp()
        _save(document)
        return _public(record)


def activate_credential(credential_id: str) -> dict[str, Any]:
    with _LOCK:
        document = _load()
        record = next((item for item in document["credentials"] if item.get("credential_id") == credential_id), None)
        if not isinstance(record, dict):
            raise CredentialVaultError("credential not found")
        kind = _validate_kind(str(record.get("kind") or ""))
        now = _timestamp()
        for item in document["credentials"]:
            if isinstance(item, dict) and item.get("kind") == kind:
                item["active"] = item is record
                item["enabled"] = True if item is record else bool(item.get("enabled"))
                item["updated_at"] = now
        _save(document)
        return _public(record)


def delete_credential(credential_id: str) -> None:
    with _LOCK:
        document = _load()
        record = next((item for item in document["credentials"] if item.get("credential_id") == credential_id), None)
        if not isinstance(record, dict):
            raise CredentialVaultError("credential not found")
        if bool(record.get("active")):
            raise CredentialVaultError("activate another credential before deleting the active one")
        document["credentials"].remove(record)
        _save(document)


def active_secret(kind: str) -> str | None:
    clean_kind = _validate_kind(kind)
    with _LOCK:
        document = _load()
        record = next(
            (
                item
                for item in document["credentials"]
                if isinstance(item, dict)
                and item.get("kind") == clean_kind
                and item.get("enabled")
                and item.get("active")
            ),
            None,
        )
        if record is None:
            return None
        try:
            return _fernet().decrypt(str(record.get("ciphertext") or "").encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise CredentialVaultError("active credential cannot be decrypted") from exc
