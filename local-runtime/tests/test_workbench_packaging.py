from pathlib import Path


def test_workbench_spec_packages_pod_customization_migrations() -> None:
    spec = Path(__file__).parents[1] / "workbench.spec"

    assert '"wh_local/modules/pod_customization/migrations"' in spec.read_text(
        encoding="utf-8"
    )


def test_installer_packages_the_verified_clipforge_app_root() -> None:
    script = (Path(__file__).parents[1] / "build_installer.ps1").read_text(encoding="utf-8")

    assert "--output-root" in script
    assert "current.json" in script
    assert "--verify-root" in script
    assert 'Join-Path $clipforgeBundle "app"' in script
    assert 'Join-Path $clipforgeApp "server.js"' in script
    assert '.next\\standalone' not in script
    assert "bundle-standalone.mjs" not in script
