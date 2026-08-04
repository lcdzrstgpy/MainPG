from __future__ import annotations

import ast
import json
import socket
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.quote_normalizer import (  # noqa: E402
    normalize_price_quote_discovery,
)


def quote_fixture() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "temu_quote_popup_dom.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_flow_does_not_open_a_network_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("network forbidden"),
    )

    assert normalize_price_quote_discovery(quote_fixture()).counts.complete_quotes == 1


def test_public_module_exports_only_registration_surface() -> None:
    import wh_local.price_verification as price_verification
    from wh_local.price_verification import (
        PriceVerificationRouteDependencies,
        register_price_verification_routes,
    )

    assert price_verification.__all__ == [
        "PriceVerificationRouteDependencies",
        "register_price_verification_routes",
    ]
    assert PriceVerificationRouteDependencies is not None
    assert callable(register_price_verification_routes)


def test_host_imports_actor_from_contracts_not_package_root() -> None:
    main_path = Path(__file__).resolve().parents[2] / "wh_local" / "app" / "main.py"
    module = ast.parse(main_path.read_text(encoding="utf-8"))

    imports = [
        (node.level, node.module, {alias.name for alias in node.names})
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
    ]

    assert all(
        not (level == 2 and module == "price_verification" and "PriceVerificationActor" in names)
        for level, module, names in imports
    )
    assert any(
        level == 2 and module == "price_verification.contracts" and "PriceVerificationActor" in names
        for level, module, names in imports
    )
