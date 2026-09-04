"""Learned theme pool registry.

Built-in theme pools (``prompts._THEME_MOTIFS``) are static and only cover a few
common themes. This module lets the registry grow at runtime: when a theme shows
up that has no usable pool, Doubao generates a set of distinct surface-design
subjects, which are validated, persisted to a JSON file, and layered over the
built-in pools on the next load.

Subjects are REUSED across styles within a theme (variety comes from the full
recipe: subject x composition x palette x rendering x density), so a generated
pool of ~15-25 subjects is enough to keep a large batch on-theme and distinct.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

# A complete() callable: OpenAI-style messages list -> str. Matches
# DauBaoArkClient.complete. Kept as a Protocol-ish alias for testing.
CompleteFn = Callable[[list[dict[str, Any]]], str]

DEFAULT_POOL_SIZE = 20
MIN_POOL_SIZE = 3
_MAX_SUBJECT_LENGTH = 80


def generate_theme_pool(theme: str, complete: CompleteFn, count: int = DEFAULT_POOL_SIZE) -> list[str]:
    """Ask Doubao for ``count`` distinct, on-theme surface-design subjects."""
    count = max(MIN_POOL_SIZE, min(int(count), 40))
    prompt = (
        f"Give {count} short, distinct, printer-ready POD surface design subjects for the theme "
        f"'{theme}'. Each is a concise English noun-phrase subject (for example 'rolling ocean waves' "
        f"or 'schools of fish and bubbles'), never a full sentence. They must be clearly different from "
        f"one another, stay on the theme, and contain no text, logo, brand, person, or product mockup. "
        f"Return ONLY a JSON array of exactly {count} strings, nothing else."
    )
    content = complete([{"role": "user", "content": prompt}])
    return _parse_subjects(content, count)


def _parse_subjects(content: str, count: int) -> list[str]:
    """Extract a validated, deduped, bounded list of subject phrases."""
    if not content:
        return []
    subjects: list[str] = []
    payload = content.strip()
    start = payload.find("[")
    end = payload.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(payload[start : end + 1])
            if isinstance(parsed, list):
                subjects = [str(item) for item in parsed]
        except (ValueError, TypeError):
            subjects = []
    if not subjects:
        # Fallback: split on line breaks, strip list markers/bullets.
        subjects = re.split(r"[,\n;]+", payload.strip().strip("[]"))
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in subjects:
        phrase = re.sub(r"^\s*[\d.\-*·)]+\s*", "", item.strip())
        phrase = phrase.strip().strip('"').strip()
        if not phrase or len(phrase) > _MAX_SUBJECT_LENGTH:
            continue
        lowered = phrase.lower()
        if lowered in seen:
            continue
        if any(word in lowered for word in ("logo", "watermark", "copyright", "brand")):
            continue
        seen.add(lowered)
        cleaned.append(phrase)
        if len(cleaned) >= count:
            break
    return cleaned


class ThemeRegistry:
    """Persist and reuse Doubao-learned theme pools, layered over built-ins."""

    def __init__(
        self,
        path: str | Path,
        *,
        builtin: dict[str, Any] | None = None,
        complete: CompleteFn | None = None,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._path = Path(path)
        if builtin is None:
            # Default to the built-in theme pools so a registry's pools() view
            # always includes the static themes plus any learned ones.
            from .prompts import _THEME_MOTIFS

            builtin = _THEME_MOTIFS
        self._builtin: dict[str, Any] = dict(builtin)
        self._complete = complete
        self._pool_size = pool_size
        self._learned: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            for key, value in data.items():
                if isinstance(key, str) and isinstance(value, list):
                    cleaned = [str(item).strip() for item in value if str(item).strip()]
                    if cleaned:
                        self._learned[key] = cleaned

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._learned, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # Non-fatal: learned pools simply won't survive a restart.
            pass

    def pools(self) -> dict[str, Any]:
        """Merged view of built-in and learned pools (theme label -> subjects)."""
        with self._lock:
            merged = dict(self._builtin)
            merged.update(self._learned)
            return merged

    def subjects(self, theme: str) -> list[str] | None:
        with self._lock:
            if theme in self._learned:
                return list(self._learned[theme])
            value = self._builtin.get(theme)
            return list(value) if value else None

    def has_pool(self, theme: str) -> bool:
        return self.subjects(theme) is not None

    def ensure(self, theme: str, count: int | None = None) -> list[str] | None:
        """Return subjects for ``theme``, generating + persisting a pool on first sight.

        If no ``complete`` callable is configured, or generation fails, returns the
        built-in pool for the theme (if any) or ``None`` so the prompt layer falls
        back to "invent a subject within the theme".
        """
        count = count or self._pool_size
        existing = self.subjects(theme)
        if existing:
            return existing
        # Claim the theme so concurrent calls don't generate duplicates.
        with self._lock:
            if theme in self._in_flight:
                return self._builtin.get(theme)
            self._in_flight.add(theme)
        try:
            if self._complete is None:
                return self._builtin.get(theme)
            generated = generate_theme_pool(theme, self._complete, count=count)
        finally:
            with self._lock:
                self._in_flight.discard(theme)
        if not generated:
            return self._builtin.get(theme)
        with self._lock:
            self._learned[theme] = generated
            self._save()
        return list(generated)
