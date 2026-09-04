from __future__ import annotations

import json

from wh_local.modules.pod_customization.theme_registry import (
    DEFAULT_POOL_SIZE,
    ThemeRegistry,
    generate_theme_pool,
)


class _FakeComplete:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append({"messages": messages})
        return self._payload


def _json_payload(subjects: list[str]) -> str:
    return json.dumps(subjects, ensure_ascii=False)


def test_generate_theme_pool_parses_and_bounds() -> None:
    payload = _json_payload([
        "rolling ocean waves",
        "schools of fish",
        "seashells and starfish",
        "rolling ocean waves",  # duplicate
        "a brand logo mark",
        "x" * 300,
    ])
    client = _FakeComplete(payload)
    subjects = generate_theme_pool("ocean", client, count=20)
    assert "rolling ocean waves" in subjects
    assert "schools of fish" in subjects
    # duplicate removed
    assert subjects.count("rolling ocean waves") == 1
    # banned word removed
    assert all("logo" not in s for s in subjects)
    # oversize removed
    assert all(len(s) <= 80 for s in subjects)
    # capped at count
    assert len(subjects) <= 20


def test_generate_theme_pool_splits_non_json() -> None:
    client = _FakeComplete("1. waves\n2. fish\n- shells\n")
    subjects = generate_theme_pool("ocean", client, count=10)
    assert "waves" in subjects
    assert "fish" in subjects
    assert "shells" in subjects


def test_registry_layers_learned_over_builtin() -> None:
    builtin = {"ocean": ["waves", "fish"]}
    reg = ThemeRegistry("/tmp/nonexistent-registry.json", builtin=builtin)
    assert reg.subjects("ocean") == ["waves", "fish"]
    # built-in wins for a theme not yet learned


def test_registry_ensure_generates_and_persists(tmp_path) -> None:
    client = _FakeComplete(_json_payload(["sleeping cat", "cat with coffee", "kitten paw"]))
    reg = ThemeRegistry(tmp_path / "reg.json", builtin={}, complete=client, pool_size=3)
    assert reg.has_pool("喵星人咖啡") is False

    subjects = reg.ensure("喵星人咖啡", count=3)
    assert subjects == ["sleeping cat", "cat with coffee", "kitten paw"]
    assert reg.has_pool("喵星人咖啡") is True
    # persisted to disk
    assert (tmp_path / "reg.json").exists()

    # reload a fresh registry from the same file
    reg2 = ThemeRegistry(tmp_path / "reg.json", builtin={})
    assert reg2.subjects("喵星人咖啡") == ["sleeping cat", "cat with coffee", "kitten paw"]


def test_registry_ensure_is_cached_and_noop_without_client(tmp_path) -> None:
    client = _FakeComplete(_json_payload(["a", "b", "c"]))
    reg = ThemeRegistry(tmp_path / "a.json", builtin={}, complete=client, pool_size=3)
    first = reg.ensure("主题甲", count=3)
    second = reg.ensure("主题甲", count=3)
    assert first == second
    assert len(client.calls) == 1  # only one generation call

    reg_no_client = ThemeRegistry(tmp_path / "b.json", builtin={}, complete=None)
    assert reg_no_client.ensure("主题乙", count=3) is None


def test_registry_pools_merges_builtin_and_learned(tmp_path) -> None:
    client = _FakeComplete(_json_payload(["sleeping cat"]))
    reg = ThemeRegistry(tmp_path / "c.json", builtin={"ocean": ["waves"]}, complete=client, pool_size=1)
    reg.ensure("喵星人咖啡", count=1)
    pools = reg.pools()
    assert pools["ocean"] == ["waves"]
    assert pools["喵星人咖啡"] == ["sleeping cat"]
