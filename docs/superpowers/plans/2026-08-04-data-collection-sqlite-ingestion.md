# 数据采集 SQLite 入库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 验证并锁定“1688/Temu 采集得到数据后，按既定字段写入统一 SQLite”的完整链路。

**Architecture:** 保留现有 Provider、Normalizer、Service、Repository 和插件队列边界。通过宿主路由注入同一个临时 `database_path`，使用 Fake Provider 产生确定性采集结果，再直接查询 SQLite 验证规范化字段、完整 JSON 快照和 Temu 结果均已写入。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLite、pytest、FastAPI TestClient。

## Global Constraints

- 不修改现有数据库表结构、字段名称或 HTTP 接口。
- 不导入 `local-runtime/outputs/api-cache` 历史调试缓存。
- 不调用真实 OneBound 或 Temu 网络接口。
- 1688 结果写入 `daily_selection_runs` 和 `daily_selection_candidates`。
- Temu 结果写入 `data_collection_plugin_commands.result_json`，不转换为 1688 候选。
- 所有测试使用临时 SQLite，并禁止外部网络访问。

---

## File Map

- Create: `local-runtime/tests/data_collection/test_sqlite_ingestion.py` — 覆盖采集、规范化、路由、共享 SQLite 与直接数据库断言。
- Existing: `local-runtime/wh_local/data_collection/service.py` — 1688 采集后调用 `save_run()`；验收测试只消费该接口。
- Existing: `local-runtime/wh_local/data_collection/repository.py` — 写入批次与候选；测试直接检查其表结果。
- Existing: `local-runtime/wh_local/data_collection/plugin_queue.py` — 写入 Temu 命令状态和结果；测试通过路由驱动。
- Existing: `local-runtime/wh_local/db.py` — 初始化统一 SQLite 和模块迁移；测试以此创建数据库。

### Task 1: 锁定 1688 采集到 SQLite 的完整链路

**Files:**
- Create: `local-runtime/tests/data_collection/test_sqlite_ingestion.py`

**Interfaces:**
- Consumes: `init_db(Path) -> None`、`register_daily_selection_routes(APIRouter, DailySelectionRouteDependencies) -> None`、`ProviderCallResult`。
- Produces: 三个离线验收测试，分别覆盖关键词、图片和相似链接采集的数据库结果。

- [ ] **Step 1: 创建 Fake Provider、测试应用和数据库查询辅助函数**

在测试文件中定义：

```python
class FakeProvider:
    credential_fingerprint = "f" * 64

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        return ProviderCallResult(
            response={"data": {"items": [_search_item("keyword-offer")]}},
            audits=(_evidence("item_search"),),
        )

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult:
        return ProviderCallResult(
            response={"data": {"items": [_search_item("image-offer")]}},
            audits=(
                _evidence("download_reference_image"),
                _evidence("upload_img"),
                _evidence("item_search_img"),
            ),
        )

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        return ProviderCallResult(
            response={"data": _detail_item(offer_id)},
            audits=(_evidence("item_get"),),
        )
```

辅助函数 `_client(database_path, run_ids)` 必须先调用 `init_db(database_path)`，再把同一个 `database_path` 传给 `DailySelectionRouteDependencies`。Actor 固定为 `actor_id="ingestion-user"`、`workspace_id="ingestion-workspace"`，`run_id_factory` 从 `run_ids` 迭代器取值。

- [ ] **Step 2: 写关键词采集入库验收测试**

```python
def test_keyword_collection_persists_normalized_candidate_in_shared_sqlite(tmp_path, no_network):
    database_path = tmp_path / "workbench.sqlite3"
    with _client(database_path, iter(("run-keyword",))) as client:
        response = client.post(
            "/desktop/daily-selection/preview",
            json={
                "keywords": ["露营灯"],
                "selection_scope": "exact",
                "target_count": 1,
                "detail_count": 1,
                "max_api_calls": 2,
            },
        )
    assert response.status_code == 200, response.text
```

随后直接查询 `daily_selection_runs` 和 `daily_selection_candidates`，断言：

```python
assert run_row == ("ingestion-workspace", "run-keyword", "completed", 1)
assert candidate_row[0:6] == (
    "1688:keyword-offer",
    "keyword-offer",
    "1688",
    "https://detail.1688.com/keyword-offer.html",
    "测试商品 keyword-offer",
    "12.30",
)
snapshot = json.loads(candidate_row[6])
assert snapshot["source_variant_records"][0]["sku_id"] == "keyword-offer-red"
assert snapshot["source_detail_image_urls"] == [
    "https://images.example.test/keyword-offer/detail.jpg"
]
```

- [ ] **Step 3: 运行关键词测试，确认合并后的链路状态**

Run:

```bash
cd local-runtime && python -m pytest tests/data_collection/test_sqlite_ingestion.py::test_keyword_collection_persists_normalized_candidate_in_shared_sqlite -q
```

Expected: PASS。若失败，失败位置必须明确落在 Provider→Normalizer、Service→Repository 或数据库字段断言之一；不得通过放宽字段断言规避不匹配。

- [ ] **Step 4: 写图片和相似链接入库验收测试**

图片测试调用 `/desktop/daily-selection/preview`，请求必须包含：

```python
{
    "collection_mode": "image",
    "reference_image_url": "https://images.example.test/reference.jpg",
    "keywords": ["露营灯"],
    "selection_scope": "exact",
    "target_count": 1,
    "detail_count": 1,
    "max_api_calls": 4,
}
```

相似链接测试调用 `/desktop/daily-selection/preview-from-1688-link`：

```python
{
    "source_url": "https://detail.1688.com/offer/123456.html",
    "selection_scope": "exact",
    "target_count": 1,
    "detail_count": 1,
    "max_api_calls": 4,
}
```

两个测试都直接查询相同 SQLite 文件，断言各自的 run、candidate、`raw_candidate_json` 已写入；相似链接的 `metadata_json.source_link.offer_id` 必须为 `123456`。

- [ ] **Step 5: 运行全部 1688 入库测试**

Run:

```bash
cd local-runtime && python -m pytest tests/data_collection/test_sqlite_ingestion.py -k 'keyword or image or similar' -q
```

Expected: 3 passed，且无网络访问。

- [ ] **Step 6: 提交 1688 入库验收测试**

```bash
git add local-runtime/tests/data_collection/test_sqlite_ingestion.py
git commit -m "test: verify 1688 collection sqlite ingestion"
```

### Task 2: 锁定 Temu 插件结果到 SQLite 的完整链路

**Files:**
- Modify: `local-runtime/tests/data_collection/test_sqlite_ingestion.py`

**Interfaces:**
- Consumes: Task 1 的 `_client(database_path, run_ids)`，以及既有 Temu 插件路由。
- Produces: `test_temu_plugin_result_persists_sanitized_json_in_shared_sqlite`。

- [ ] **Step 1: 写 Temu 插件结果入库测试**

测试按真实状态流转调用路由：

```python
session = client.post(
    "/desktop/data-collection/plugin-sessions",
    json={"temu_link_capture": True},
).json()
queued = client.post(
    "/desktop/data-collection/temu-link/collect",
    json={
        "session_id": session["session_id"],
        "source_url": "https://www.temu.com/goods.html?goods_id=123",
    },
).json()
client.get(
    "/desktop/data-collection/plugin/poll",
    params={"session_token": session["session_token"]},
)
result = client.post(
    "/desktop/data-collection/plugin/results",
    json={
        "session_token": session["session_token"],
        "command_id": queued["command_id"],
        "status": "succeeded",
        "result": {
            "source_url": "https://www.temu.com/goods.html?goods_id=123",
            "title": "Temu 测试商品",
            "price": "19.90",
            "currency": "CNY",
            "authorization": "Bearer must-not-persist",
        },
    },
)
assert result.status_code == 200, result.text
```

直接查询 `data_collection_plugin_commands`：

```python
status, result_json = connection.execute(
    "SELECT status, result_json FROM data_collection_plugin_commands WHERE id = ?",
    (queued["command_id"],),
).fetchone()
assert status == "succeeded"
stored = json.loads(result_json)
assert stored["title"] == "Temu 测试商品"
assert stored["price"] == "19.90"
assert "authorization" not in stored
assert "must-not-persist" not in result_json
```

- [ ] **Step 2: 运行 Temu 入库测试**

Run:

```bash
cd local-runtime && python -m pytest tests/data_collection/test_sqlite_ingestion.py::test_temu_plugin_result_persists_sanitized_json_in_shared_sqlite -q
```

Expected: PASS，数据库命令状态为 `succeeded`，敏感字段未持久化。

- [ ] **Step 3: 运行本任务离线验收套件**

Run:

```bash
cd local-runtime && python -m pytest tests/data_collection/test_sqlite_ingestion.py -q
```

Expected: 4 passed，无 DNS、Socket 或真实 API 调用。

- [ ] **Step 4: 运行编译与数据库迁移验证**

Run:

```bash
cd local-runtime && python -m compileall -q wh_local tests/data_collection/test_sqlite_ingestion.py
```

Expected: exit 0。

Run:

```bash
cd local-runtime && python -c "from pathlib import Path; from tempfile import TemporaryDirectory; from wh_local.db import init_db; import sqlite3; d=TemporaryDirectory(); p=Path(d.name)/'workbench.sqlite3'; init_db(p); c=sqlite3.connect(p); required={'daily_selection_runs','daily_selection_candidates','data_collection_plugin_sessions','data_collection_plugin_commands'}; actual={r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}; assert required <= actual; print('migration-ok')"
```

Expected: 输出 `migration-ok`。

- [ ] **Step 5: 提交 Temu 入库验收测试**

```bash
git add local-runtime/tests/data_collection/test_sqlite_ingestion.py
git commit -m "test: verify Temu result sqlite ingestion"
```
