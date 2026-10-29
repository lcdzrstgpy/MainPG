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


def test_workbench_spec_packages_downloadable_themes() -> None:
    """主题商店的可下载主题包必须打进包。

    2026-10-22：以前漏了这一项，安装版 ``/themes`` 恒返回 ``{"themes": []}``，
    主题商店里 chinese / violet / dessert / diamond / quirky 五个主题永远下不到
    （内置的只有 classic/sunset/peach/alpine）。后端按 ``wh_local/data/themes``
    相对位置找目录，打包后落在 ``_internal`` 下，正好命中 main.py 的第二个候选路径。
    """
    spec = Path(__file__).parents[1] / "workbench.spec"
    text = spec.read_text(encoding="utf-8")

    # 2026-10-29：从只收 data/themes 改成收整个 wh_local/data ——
    # 否则同目录下新增的 temu_gateway.json（服务商凭据随包兜底）打不进包。
    assert '"wh_local/data"' in text, "spec 必须打包整个 wh_local/data"
    data_dir = Path(__file__).parents[1] / "wh_local" / "data"
    themes_dir = data_dir / "themes"
    assert themes_dir.is_dir(), "主题包目录本身也得存在，否则打包进去的是空目录"
    assert any(themes_dir.iterdir()), "主题包目录不能为空"
    assert (data_dir / "temu_gateway.json").is_file(), "服务商凭据兜底文件要在 data 目录里"
