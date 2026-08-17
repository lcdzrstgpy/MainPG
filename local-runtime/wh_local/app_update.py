"""Signed Windows desktop update checks and installer hand-off."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request


MANIFEST_FIELDS = (
    "version",
    "mandatory",
    "installer_url",
    "sha256",
    "release_notes",
    "published_at",
)
ACCEPTED_MANIFEST_FIELDS = frozenset((*MANIFEST_FIELDS, "signature"))
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9A-Za-z-]+)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]+))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ManifestValidationError(ValueError):
    """The supplied release manifest cannot safely be used."""


@dataclass(frozen=True)
@total_ordering
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = _SEMVER_RE.fullmatch(value)
        if not match:
            raise ValueError(f"invalid semantic version: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        has_leading_zero = any(
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
            for identifier in prerelease
        )
        if has_leading_zero:
            raise ValueError(f"invalid semantic version: {value!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class UpdateSettings:
    current_version: str
    manifest_url: str
    allowed_hosts: frozenset[str]
    public_key_b64: str
    runtime_root: Path
    platform: str = sys.platform


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    mandatory: bool
    installer_url: str
    sha256: str
    release_notes: str
    published_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mandatory": self.mandatory,
            "installer_url": self.installer_url,
            "sha256": self.sha256,
            "release_notes": self.release_notes,
            "published_at": self.published_at,
        }


def canonical_manifest_payload(manifest: Mapping[str, object]) -> bytes:
    """Serialize the signed contract deterministically, excluding signature."""
    return json.dumps(
        {name: manifest.get(name) for name in MANIFEST_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class UpdateManager:
    def __init__(
        self,
        settings: UpdateSettings,
        manifest_fetcher: Callable[[str], Mapping[str, object] | str | bytes] | None = None,
        downloader: Callable[[str], Any] | None = None,
        launcher: Callable[[Path, list[str]], None] | None = None,
    ) -> None:
        self.settings = settings
        self._validate_allowed_url(settings.manifest_url)
        SemanticVersion.parse(settings.current_version)
        try:
            public_key = base64.b64decode(settings.public_key_b64, validate=True)
            self._public_key = Ed25519PublicKey.from_public_bytes(public_key)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid built-in update public key") from error
        self._manifest_fetcher = manifest_fetcher or self._fetch_manifest
        self._downloader = downloader or self._open_download
        self._launcher = launcher or self._launch_installer
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._release: UpdateRelease | None = None
        self._error: str | None = None
        self._progress: dict[str, int | float | None] | None = None

    def with_manifest_fetcher(self, fetcher: Callable[[str], Mapping[str, object] | str | bytes]) -> "UpdateManager":
        self._manifest_fetcher = fetcher
        return self

    def with_downloader(self, downloader: Callable[[str], Any]) -> "UpdateManager":
        self._downloader = downloader
        return self

    def with_launcher(self, launcher: Callable[[Path, list[str]], None]) -> "UpdateManager":
        self._launcher = launcher
        return self

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "state": self._state,
                "current_version": self.settings.current_version,
                "release": self._release.as_dict() if self._release else None,
                "progress": dict(self._progress) if self._progress else None,
                "error": self._error,
            }

    def start_check(self) -> dict[str, object]:
        if not self._begin("checking"):
            return self.status()
        threading.Thread(target=self._check_after_begin, name="mainpg-update-check", daemon=True).start()
        return self.status()

    def check(self) -> dict[str, object]:
        if not self._begin("checking"):
            return self.status()
        return self._check_after_begin()

    def _check_after_begin(self) -> dict[str, object]:
        try:
            if self.settings.platform != "win32":
                self._set_state("unavailable", error="Automatic updates are only available on Windows.")
                return self.status()
            release = self._validate_manifest(self._coerce_manifest(self._manifest_fetcher(self.settings.manifest_url)))
            if SemanticVersion.parse(release.version) <= SemanticVersion.parse(self.settings.current_version):
                self._set_state("idle", release=None)
            else:
                self._set_state("available", release=release)
        except Exception as error:
            # A transient refresh failure cannot invalidate the last signed,
            # verified release. In particular, retain its mandatory flag so a
            # retry cannot dismiss or bypass a required update.
            self._set_state("failed", error=self._safe_error(error))
        finally:
            self._operation_lock.release()
        return self.status()

    def start_install(self) -> dict[str, object]:
        if not self._begin("downloading"):
            return self.status()
        threading.Thread(target=self._install_after_begin, name="mainpg-update-install", daemon=True).start()
        return self.status()

    def install(self) -> dict[str, object]:
        if not self._begin("downloading"):
            return self.status()
        return self._install_after_begin()

    def _install_after_begin(self) -> dict[str, object]:
        installer_launched = False
        try:
            if self.settings.platform != "win32":
                self._set_state("unavailable", error="Automatic updates are only available on Windows.")
                return self.status()
            if self._release is None:
                raise RuntimeError("No verified update is available to install.")
            installer = self._download_verified_installer(self._release)
            self._set_state("installing")
            self._launcher(installer, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            installer_launched = True
        except Exception as error:
            self._set_state("failed", error=self._safe_error(error))
        finally:
            if not installer_launched:
                self._operation_lock.release()
        return self.status()

    def _begin(self, state: str) -> bool:
        if not self._operation_lock.acquire(blocking=False):
            return False
        self._set_state(state, error=None, progress=None)
        return True

    def _set_state(
        self,
        state: str,
        *,
        release: UpdateRelease | None | object = ...,
        error: str | None | object = ...,
        progress: dict[str, int | float | None] | None | object = ...,
    ) -> None:
        with self._state_lock:
            self._state = state
            if release is not ...:
                self._release = release  # type: ignore[assignment]
            if error is not ...:
                self._error = error  # type: ignore[assignment]
            if progress is not ...:
                self._progress = progress  # type: ignore[assignment]

    def _validate_manifest(self, manifest: Mapping[str, object]) -> UpdateRelease:
        if set(manifest) != ACCEPTED_MANIFEST_FIELDS:
            raise ManifestValidationError("Update manifest fields do not match the signed schema.")
        if not isinstance(manifest["signature"], str):
            raise ManifestValidationError("Update manifest signature is invalid.")
        if not isinstance(manifest["version"], str):
            raise ManifestValidationError("Update manifest version is invalid.")
        SemanticVersion.parse(manifest["version"])
        if not isinstance(manifest["mandatory"], bool):
            raise ManifestValidationError("Update manifest mandatory flag is invalid.")
        for field in ("installer_url", "sha256", "release_notes", "published_at"):
            if not isinstance(manifest[field], str):
                raise ManifestValidationError(f"Update manifest {field} is invalid.")
        self._validate_allowed_url(manifest["installer_url"])
        if not _SHA256_RE.fullmatch(manifest["sha256"]):
            raise ManifestValidationError("Update manifest SHA-256 is invalid.")
        try:
            published = manifest["published_at"].replace("Z", "+00:00")
            if datetime.fromisoformat(published).tzinfo is None:
                raise ValueError
        except ValueError as error:
            raise ManifestValidationError("Update manifest published_at is invalid.") from error
        try:
            signature = base64.b64decode(manifest["signature"], validate=True)
            self._public_key.verify(signature, canonical_manifest_payload(manifest))
        except (InvalidSignature, ValueError, binascii.Error) as error:
            raise ManifestValidationError("Update manifest signature is invalid.") from error
        return UpdateRelease(
            version=manifest["version"],
            mandatory=manifest["mandatory"],
            installer_url=manifest["installer_url"],
            sha256=manifest["sha256"].lower(),
            release_notes=manifest["release_notes"],
            published_at=manifest["published_at"],
        )

    def _validate_allowed_url(self, value: str) -> None:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or host not in {item.lower() for item in self.settings.allowed_hosts}
            or parsed.username
            or parsed.password
        ):
            raise ManifestValidationError("Update URL must use an allowlisted HTTPS host.")

    @staticmethod
    def _coerce_manifest(value: Mapping[str, object] | str | bytes) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return value
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ManifestValidationError("Update manifest is not valid JSON.") from error
        if not isinstance(decoded, Mapping):
            raise ManifestValidationError("Update manifest must be an object.")
        return decoded

    def _validate_response_url(self, response: Any) -> None:
        get_url = getattr(response, "geturl", None)
        if callable(get_url):
            final_url = get_url()
            if not isinstance(final_url, str):
                raise ManifestValidationError("Update response URL is invalid.")
            self._validate_allowed_url(final_url)

    def _fetch_manifest(self, url: str) -> Mapping[str, object] | str | bytes:
        with urlopen(url, timeout=10) as response:  # nosec B310: URL is allowlisted before use
            self._validate_response_url(response)
            return response.read()

    @staticmethod
    def _open_download(url: str) -> Any:
        return urlopen(url, timeout=30)  # nosec B310: URL is manifest-validated

    def _download_verified_installer(self, release: UpdateRelease) -> Path:
        updates_dir = self.settings.runtime_root / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        destination = updates_dir / f"MainPG-{release.version}.exe"
        partial = destination.with_suffix(".exe.part")
        digest = hashlib.sha256()
        downloaded = 0
        source = self._downloader(release.installer_url)
        try:
            self._validate_response_url(source)
            total = self._content_length(source)
            with partial.open("wb") as output:
                for chunk in self._download_chunks(source):
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    percentage = round(downloaded * 100 / total, 2) if total else None
                    self._set_state(
                        "downloading",
                        progress={"downloaded_bytes": downloaded, "total_bytes": total, "percentage": percentage},
                    )
            self._set_state("verifying")
            if digest.hexdigest().lower() != release.sha256:
                raise RuntimeError("Downloaded installer SHA-256 does not match the signed manifest.")
            os.replace(partial, destination)
            return destination
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            close = getattr(source, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _download_chunks(source: Any) -> Iterable[bytes]:
        if hasattr(source, "read"):
            while chunk := source.read(1024 * 256):
                yield chunk
            return
        for chunk in source:
            if not isinstance(chunk, bytes):
                raise RuntimeError("Installer download returned non-binary data.")
            if chunk:
                yield chunk

    @staticmethod
    def _content_length(source: Any) -> int | None:
        headers = getattr(source, "headers", None)
        raw = headers.get("Content-Length") if headers else None
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _launch_installer(path: Path, args: list[str]) -> None:
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(path), *args], cwd=str(path.parent), creationflags=flags)  # noqa: S603

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return str(error) or "Update failed. Please try again later."


def create_router(manager: UpdateManager) -> APIRouter:
    router = APIRouter(prefix="/api/app-update", tags=["app-update"])

    def require_same_origin(request: Request) -> None:
        local_origin = _normalized_origin(str(request.base_url))
        if (
            local_origin is None
            or local_origin[0] != "http"
            or local_origin[1] not in _LOCAL_HTTP_HOSTS
        ):
            raise HTTPException(status_code=403, detail="Cross-origin update actions are not allowed.")
        origin = request.headers.get("origin")
        if origin is None:
            return
        supplied_origin = _normalized_origin(origin)
        if supplied_origin is None or supplied_origin != local_origin:
            raise HTTPException(status_code=403, detail="Cross-origin update actions are not allowed.")

    @router.get("/status")
    def update_status() -> dict[str, object]:
        return manager.status()

    @router.post("/check")
    def check_for_update(request: Request) -> dict[str, object]:
        require_same_origin(request)
        return manager.start_check()

    @router.post("/install")
    def install_update(request: Request) -> dict[str, object]:
        require_same_origin(request)
        return manager.start_install()

    return router


def _normalized_origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), port
