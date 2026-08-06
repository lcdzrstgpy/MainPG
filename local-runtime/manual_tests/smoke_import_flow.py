"""Smoke test: exercise the prototype-style form flow through /import end to end."""
import io
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.app.main import create_app  # noqa: E402


def build_workbook_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "products"
    ws.append(["SKC", "标题", "类目", "主图", "来源链接", "描述", "成本", "申报价"])
    ws.append(
        [
            "SMK-001",
            "Portable Camping Chair with Cup Holder",
            "Outdoor Furniture",
            "https://example.com/images/chair.jpg",
            "https://example.com/products/chair",
            "Lightweight folding chair",
            15.5,
            22.9,
        ]
    )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "smoke.sqlite3"
        app = create_app(database_path=db_path)
        client = TestClient(app)
        headers = {"X-Workspace-ID": "smoke-workspace"}

        form = {
            "title": "冒烟测试-导入处理",
            "target_site": "US",
            "target_language": "en",
            "processing_scope": "title,details,product_dimensions,four_grid,detail_images",
            "qualification_mode": "strict",
            "include_product_video": "true",
            "max_products": "10",
        }
        files = {
            "file": (
                "products.xlsx",
                build_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        response = client.post("/api/product-processing/import", headers=headers, data=form, files=files)
        print("import status:", response.status_code)
        if response.status_code != 200:
            print("body:", response.text)
            raise SystemExit(1)
        data = response.json()
        task_id = data["task_id"]
        print("task_id:", task_id)
        print("target_site:", data["target_site"])
        print("processing_scope:", data["processing_scope"])
        print("qualification_mode:", data["qualification_mode"])
        print("include_product_video:", data["include_product_video"])
        print("item counts:", {k: v for k, v in data["manifest"]["item_counts"].items()})
        print("artifacts:", [a["kind"] for a in data["artifacts"]])

        # History list
        history = client.get("/api/product-processing/tasks/history?limit=10", headers=headers)
        print("history status:", history.status_code, "tasks:", len(history.json()["tasks"]))

        # Outputs endpoint includes prototype fields
        outputs = client.get(f"/api/product-processing/tasks/{task_id}/outputs", headers=headers)
        out = outputs.json()
        print("outputs manifest:", out["manifest"]["manifest_id"], "artifacts:", len(out["artifacts"]))

        # Failure classification keys present
        for key in ("attention_required_count", "technical_retryable_count", "configuration_blocked_count",
                    "identity_review_required_count", "logistics_review_required_count"):
            assert key in out, f"missing {key}"
        print("all smoke checks passed")


if __name__ == "__main__":
    main()
