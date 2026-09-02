from pathlib import Path


def test_collection_progress_uses_python310_compatible_utc_timezone() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "wh_local"
        / "data_collection"
        / "progress.py"
    ).read_text(encoding="utf-8")

    assert "from datetime import UTC" not in source
    assert "timezone.utc" in source
