"""Local credential loading for the MainPG workbench.

This module loads collection/upload credentials from candidate config files and
never logs or exposes secret values.

``json_candidates`` are tried first (dev checkouts and older installs ship
``*.local.json`` plaintext, e.g. ``wh_local/onebound.local.json``).  If none
parses, ``enc_candidates`` are tried: the packaged installer encrypts the plain
JSON into ``*.enc`` (AES-256-GCM) so plaintext secrets are not shipped to
customer machines, and this module decrypts them at runtime.  This is why the
installed app works with only ``cos.enc`` / ``onebound.enc`` and no
``*.local.json``.

The encryption key is a fixed constant baked into the module: the ``*.enc``
blob is produced on the *release build* machine and decrypted on the *customer*
machine, so the key cannot derive from machine identity / environment variables
(those differ between the two hosts).  This is a deterrence against simply
copying ``*.local.json``, not a secret kept away from the executable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

# Magic header so encrypted blobs are self-describing and easy to distinguish
# from any stray plaintext file.  The rest is ``nonce(12) || ciphertext+tag``.
_ENC_MAGIC = b"MAINPGENC:"
_ENC_KEY_SEED = b"mainpg-wh-local-credential-key-v1"


def _credential_key() -> bytes:
    # Fixed key shared by build-time encrypt and runtime decrypt.
    return hashlib.sha256(_ENC_KEY_SEED).digest()


def _aead() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:  # pragma: no cover - guarded by tooling
        raise RuntimeError(
            "cryptography is required to load encrypted credential files"
        ) from exc
    return AESGCM(_credential_key())


def encrypt_bytes(raw: bytes) -> bytes:
    """Encrypt ``raw`` bytes into a self-describing ``*.enc`` blob."""
    nonce = os.urandom(12)
    ciphertext = _aead().encrypt(nonce, raw, None)
    return _ENC_MAGIC + nonce + ciphertext


def decrypt_bytes(blob: bytes) -> bytes:
    """Decrypt a blob produced by :func:`encrypt_bytes`.

    Raises ``ValueError`` if the blob is not a recognized encrypted payload or
    the key does not authenticate it.
    """
    if not blob.startswith(_ENC_MAGIC):
        raise ValueError("unrecognized encrypted credential blob")
    body = blob[len(_ENC_MAGIC):]
    nonce, ciphertext = body[:12], body[12:]
    return _aead().decrypt(nonce, ciphertext, None)


def encrypt_credential_file(source: Path, dest: Path) -> None:
    """Read plaintext JSON from ``source`` and write ``dest`` as ``*.enc``.

    A leading UTF-8 BOM (sometimes added by editors / PowerShell) is stripped so
    the decrypted payload is always clean JSON.
    """
    raw = Path(source).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    Path(dest).write_bytes(encrypt_bytes(raw))


def decrypt_credential_file(path: Path) -> Mapping[str, Any]:
    """Read and decrypt an ``*.enc`` credential file into a mapping."""
    raw = decrypt_bytes(Path(path).read_bytes())
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, Mapping) else {}


def load_credential_config(
    json_candidates: Sequence[Path | str] = (),
    enc_candidates: Sequence[Path | str] = (),
    name: str = "",
) -> Mapping[str, Any] | None:
    """Return the first parseable credential mapping, or ``None``.

    Plaintext ``*.local.json`` candidates are tried first (dev checkouts and
    older installs).  When none parses, encrypted ``*.enc`` candidates are
    decrypted (packaged installs ship only the encrypted blob).  The ``name``
    argument exists for callers that load several credential families and is
    not encoded into the file format.  No key material is ever echoed.
    """
    for candidate in json_candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return value if isinstance(value, Mapping) else {}

    # Packaged builds ship only the encrypted blob; decrypt the first file that
    # is present and authenticates with the shared key.
    for candidate in enc_candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            return decrypt_credential_file(path)
        except (OSError, ValueError):
            continue
    return None


def _cmd_encrypt(args: argparse.Namespace) -> int:
    encrypt_credential_file(Path(args.source), Path(args.dest))
    return 0


def _cmd_decrypt(args: argparse.Namespace) -> int:
    value = decrypt_credential_file(Path(args.source))
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wh_local.secrets",
        description="encrypt / decrypt MainPG credential files",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Signature matches build_installer.ps1:
    #   python -m wh_local.secrets encrypt <source> <dest.enc> <name>
    enc = sub.add_parser("encrypt", help="read plaintext JSON, write an *.enc blob")
    enc.add_argument("source", help="plaintext JSON file to encrypt")
    enc.add_argument("dest", help="output *.enc path")
    enc.add_argument("name", nargs="?", default="", help="credential family label (cos/onebound)")
    enc.set_defaults(func=_cmd_encrypt)

    dec = sub.add_parser("decrypt", help="decrypt an *.enc blob and print the JSON")
    dec.add_argument("source", help="*.enc file to decrypt")
    dec.set_defaults(func=_cmd_decrypt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
