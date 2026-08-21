from __future__ import annotations

from wh_local.modules.pod_customization.errors import safe_error_message


def test_provider_error_redaction_removes_urls_headers_and_secret_values() -> None:
    raw = (
        "upstream message=https://provider.example.test/result?token=QUERY-SECRET "
        "Authorization: Bearer HEADER-SECRET api_key=KEY-SECRET reason failed"
    )

    rendered = safe_error_message(raw)

    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered
    assert "KEY-SECRET" not in rendered
    assert "?token=" not in rendered
    assert "Bearer" not in rendered
    assert "provider.example" not in rendered
    assert "reason failed" in rendered


def test_provider_error_redaction_is_single_line_and_bounded() -> None:
    rendered = safe_error_message("first\nsecond secret=" + "x" * 1000)

    assert "\n" not in rendered
    assert len(rendered) <= 500
    assert "x" * 100 not in rendered
