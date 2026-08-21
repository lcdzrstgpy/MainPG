from pathlib import Path


def test_workbench_spec_packages_pod_customization_migrations() -> None:
    spec = Path(__file__).parents[1] / "workbench.spec"

    assert '"wh_local/modules/pod_customization/migrations"' in spec.read_text(
        encoding="utf-8"
    )
