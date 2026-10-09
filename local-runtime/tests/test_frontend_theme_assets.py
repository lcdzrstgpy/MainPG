from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import wh_local.app.main as app_main


def test_frontend_shell_serves_packaged_theme_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frontend_dist = tmp_path / "web-frontend" / "dist"
    (frontend_dist / "theme").mkdir(parents=True)
    (frontend_dist / "theme" / "chinese-ink-overlay.png").write_bytes(b"ink")
    (frontend_dist / "theme" / "chinese-bamboo.png").write_bytes(b"bamboo")
    (frontend_dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    monkeypatch.setattr(app_main, "_frontend_dist_dir", lambda: frontend_dist)

    app = FastAPI()
    app_main._register_frontend_shell(app)

    with TestClient(app) as client:
        overlay = client.get("/theme/chinese-ink-overlay.png")
        bamboo = client.get("/theme/chinese-bamboo.png")

    assert overlay.status_code == 200
    assert overlay.content == b"ink"
    assert bamboo.status_code == 200
    assert bamboo.content == b"bamboo"
