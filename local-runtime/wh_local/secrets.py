"""Local credential loading for the MainPG workbench.

This module is intentionally small: it loads collection/upload credentials from
candidate config files and never logs or exposes secret values.

``json_candidates`` are tried in order (dev checkouts and older installs ship
``*.local.json`` plaintext, e.g. ``wh_local/onebound.local.json``).  If none
parses, ``enc_candidates`` would be used by the packaged build (the installer
encrypts the JSON into ``*.enc`` so plaintext secrets are not shipped to
customer machines); decryption is owned by the release packaging and is not
needed for local runs, so an unreadable/missing encrypted file yields ``None``
and callers fall back to a disabled provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def load_credential_config(
    json_candidates: Sequence[Path | str] = (),
    enc_candidates: Sequence[Path | str] = (),
    name: str = "",
) -> Mapping[str, Any] | None:
    """Return the first parseable credential mapping, or ``None``.

    OneBound (and other) configs are small JSON objects; the ``name`` argument
    exists for callers that load several credential families and is not encoded
    into the file format.  No key material is ever echoed.
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
    # Packaged builds may only ship the encrypted blob; decryption lives in the
    # release pipeline, so a local run without plaintext config stays disabled.
    return None
