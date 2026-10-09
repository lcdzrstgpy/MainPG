# Product Precheck Deferred COS Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable precheck image manifest that supports add/delete/reorder locally and publishes only the final retained images to COS before the final Dianxiaomi workbook is generated.

**Architecture:** Store image identity separately from presentation order: workspace-scoped asset rows own bytes and publication state, while `image_manifest_v2` stores ordered asset IDs and the selected main image. Product processing, dimension canvas, and user upload all register local assets; an idempotent background finalization run snapshots revisions, publishes unique content hashes with database leases, resolves trusted COS HTTPS URLs, and only then writes the final workbook.

**Tech Stack:** Python 3.14, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite WAL, Pillow, Tencent COS SDK, pytest, React 18, TypeScript 5.6, Vite 5, Node built-in test runner.

---

## Implementation invariants

- No COS write is allowed during AI generation, local upload, dimension rendering, dimension review submission, or dimension review acceptance.
- Browser-facing payloads may contain `asset_id`, `/pp-media/` preview URLs, or trusted public HTTPS URLs; they may not contain `managed_path`, Windows paths, claim tokens, or credentials.
- `image_manifest_v2.carousel_asset_ids` and `detail_asset_ids` preserve explicit empty arrays.
- Every preview mutation carries `expected_preview_revision`; stale writes return HTTP 409 and retain browser edits.
- Deleting an image removes only its manifest reference. Physical cleanup is outside this implementation.
- Finalization publishes only the immutable snapshot's live asset IDs. A missing main image, failed publication, stale revision, private URL, or local URL blocks workbook creation.
- Content-hash publication claims are workspace scoped, lease based, and token checked. Retry never re-renders or re-uploads a confirmed object.

## File structure

### New backend files

- `local-runtime/wh_local/modules/product_processing/domain/preview_images.py` — typed manifest parsing, ordered-ID normalization, semantic slot replacement, snapshot hashing.
- `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_orm.py` — asset, publication receipt, and finalization-run tables.
- `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py` — workspace-scoped asset registration, revision-safe manifest writes, publication leases, run persistence.
- `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_files.py` — strict single-frame image decoding and metadata extraction.
- `local-runtime/wh_local/modules/product_processing/preview_image_service.py` — legacy projection, local registration, finalization orchestration, progress and retry.
- `local-runtime/tests/test_product_processing_preview_images.py` — domain, local asset, manifest, migration, and workspace tests.
- `local-runtime/tests/test_product_processing_preview_finalize.py` — publication, idempotency, failure recovery, and workbook tests.

### Modified backend files

- `local-runtime/wh_local/modules/product_processing/infrastructure/database.py` — load new ORM metadata.
- `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py` — content-addressed workspace preview storage and safe lookup.
- `local-runtime/wh_local/modules/product_processing/infrastructure/media.py` — generic deterministic COS collection key.
- `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py` — require preview revisions for old save path and retain compatibility.
- `local-runtime/wh_local/modules/product_processing/service.py` — delegate preview operations, persist generated media locally, expose trusted publisher adapter.
- `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py` — remove publication from review submission.
- `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py` — accept a local dimension asset into `image_manifest_v2`.
- `local-runtime/wh_local/modules/product_processing/api/schemas.py` — typed manifest/save/finalize requests.
- `local-runtime/wh_local/modules/product_processing/api/router.py` — local asset upload and async finalize/status/retry routes.
- `local-runtime/wh_local/modules/product_processing/domain/workbooks.py` — final defense against unresolved or untrusted image values.
- `local-runtime/wh_local/modules/product_processing/README.md` — document the new lifecycle.
- `local-runtime/tests/test_product_processing_preview_overrides.py` — replace URL-array assumptions with manifest compatibility checks.
- `local-runtime/tests/test_product_processing_dimension_canvas.py` — assert local-only handoff and accepted manifest references.
- `local-runtime/tests/test_product_processing_image_quality.py` — deterministic preview COS key and HEAD reconciliation.

### New frontend files

- `web-frontend/src/modules/product_processing/data/precheckImageModel.ts` — pure immutable manifest operations and undo records.
- `web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts` — add/delete/main/reorder/empty-list contracts.
- `web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.ts` — one-in-flight, latest-wins progress polling controller.
- `web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts` — stale response, visibility and cleanup tests.
- `web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx` — source library plus main/carousel/detail asset controls.
- `web-frontend/src/modules/product_processing/components/PrecheckFinalizeProgress.tsx` — progress, failures and retry UI.

### Modified frontend files

- `web-frontend/src/modules/product_processing/types/index.ts` — asset, manifest, finalize-run and revision types.
- `web-frontend/src/modules/product_processing/api/productProcessingApi.ts` — preview asset and finalization API functions.
- `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx` — compose local edits, multi-file add/delete/reorder, finalize instead of direct export.
- `web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css` — asset cards, origin badges, delete/undo and progress states.

## Task 1: Define the versioned image manifest domain

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/domain/preview_images.py`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`

- [ ] **Step 1: Write failing manifest tests**

Create the test file with these first contracts:

```python
from wh_local.modules.product_processing.domain.preview_images import (
    PreviewImageManifest,
    replace_carousel_slot,
    snapshot_hash,
)


def test_manifest_preserves_explicit_empty_lists() -> None:
    manifest = PreviewImageManifest.from_value(
        {"main_asset_id": "", "carousel_asset_ids": [], "detail_asset_ids": []}
    )
    assert manifest.as_dict() == {
        "main_asset_id": "",
        "carousel_asset_ids": [],
        "detail_asset_ids": [],
    }


def test_manifest_deduplicates_without_reordering() -> None:
    manifest = PreviewImageManifest.from_value(
        {
            "main_asset_id": "asset-b",
            "carousel_asset_ids": ["asset-a", "asset-b", "asset-a", ""],
            "detail_asset_ids": ["asset-c", "asset-c"],
        }
    )
    assert manifest.carousel_asset_ids == ("asset-a", "asset-b")
    assert manifest.detail_asset_ids == ("asset-c",)


def test_dimension_slot_replaces_index_three_or_appends() -> None:
    current = PreviewImageManifest("asset-a", ("asset-a", "asset-b"), ())
    changed = replace_carousel_slot(current, "carousel.dimension_background", "dimension-1")
    assert changed.carousel_asset_ids == ("asset-a", "asset-b", "dimension-1")
    replaced = replace_carousel_slot(
        PreviewImageManifest("asset-a", ("asset-a", "asset-b", "asset-c", "old"), ()),
        "carousel.dimension_background",
        "dimension-2",
    )
    assert replaced.carousel_asset_ids == ("asset-a", "asset-b", "asset-c", "dimension-2")


def test_snapshot_hash_is_order_sensitive_and_key_order_stable() -> None:
    left = snapshot_hash([{"draft_id": 1, "assets": ["a", "b"]}])
    same = snapshot_hash([{"assets": ["a", "b"], "draft_id": 1}])
    reordered = snapshot_hash([{"draft_id": 1, "assets": ["b", "a"]}])
    assert left == same
    assert left != reordered
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run from the repository root:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_images.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `domain.preview_images`.

- [ ] **Step 3: Implement the manifest domain**

Create `preview_images.py` with the stable public interface below:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

MANIFEST_KEY = "image_manifest_v2"
SLOT_INDEX = {
    "carousel.hero": 0,
    "carousel.detail": 1,
    "carousel.lifestyle": 2,
    "carousel.dimension_background": 3,
}


def _ordered_ids(values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        asset_id = str(value or "").strip()
        if asset_id and asset_id not in seen:
            seen.add(asset_id)
            ordered.append(asset_id)
    return tuple(ordered)


@dataclass(frozen=True)
class PreviewImageManifest:
    main_asset_id: str = ""
    carousel_asset_ids: tuple[str, ...] = ()
    detail_asset_ids: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "PreviewImageManifest":
        raw = value if isinstance(value, dict) else {}
        return cls(
            main_asset_id=str(raw.get("main_asset_id") or "").strip(),
            carousel_asset_ids=_ordered_ids(raw.get("carousel_asset_ids") or []),
            detail_asset_ids=_ordered_ids(raw.get("detail_asset_ids") or []),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "main_asset_id": self.main_asset_id,
            "carousel_asset_ids": list(self.carousel_asset_ids),
            "detail_asset_ids": list(self.detail_asset_ids),
        }

    def live_asset_ids(self) -> tuple[str, ...]:
        return _ordered_ids(
            (self.main_asset_id, *self.carousel_asset_ids, *self.detail_asset_ids)
        )


def replace_carousel_slot(
    manifest: PreviewImageManifest, slot_id: str, asset_id: str
) -> PreviewImageManifest:
    normalized = str(asset_id or "").strip()
    if slot_id not in SLOT_INDEX or not normalized:
        raise ValueError("invalid carousel slot replacement")
    values = list(manifest.carousel_asset_ids)
    index = SLOT_INDEX[slot_id]
    if index < len(values):
        values[index] = normalized
    else:
        values.append(normalized)
    return PreviewImageManifest(
        main_asset_id=manifest.main_asset_id,
        carousel_asset_ids=_ordered_ids(values),
        detail_asset_ids=manifest.detail_asset_ids,
    )


def snapshot_hash(entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the domain tests**

Run the Task 1 pytest command. Expected: `4 passed`.

- [ ] **Step 5: Commit the manifest domain**

```powershell
git add local-runtime/wh_local/modules/product_processing/domain/preview_images.py local-runtime/tests/test_product_processing_preview_images.py
git commit -m "feat(product-processing): define preview image manifest"
```

## Task 2: Add asset, publication, and finalization persistence

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_orm.py`
- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/database.py:10-14`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`

- [ ] **Step 1: Add failing schema, workspace, and lease tests**

Append tests that create a file-backed SQLite database and assert:

```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import inspect

from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.preview_image_repository import (
    PreviewImageRepository,
    PreviewPublicationConflict,
)


def test_preview_image_schema_is_created(tmp_path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'preview.sqlite3').as_posix()}")
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "product_processing_preview_image_assets",
        "product_processing_preview_publications",
        "product_processing_preview_finalize_runs",
    } <= tables


def test_asset_identity_is_idempotent_and_workspace_scoped(tmp_path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'assets.sqlite3').as_posix()}")
    repository = PreviewImageRepository(database)
    first = repository.register_asset(
        workspace_id="a", task_id=7, product_draft_id=9, origin="upload",
        identity_hash="1" * 64, managed_path="C:/managed/a.jpg", source_url="",
        content_hash="2" * 64, content_type="image/jpeg", byte_size=3, width=20, height=20,
    )
    repeated = repository.register_asset(
        workspace_id="a", task_id=7, product_draft_id=9, origin="upload",
        identity_hash="1" * 64, managed_path="C:/managed/a.jpg", source_url="",
        content_hash="2" * 64, content_type="image/jpeg", byte_size=3, width=20, height=20,
    )
    assert repeated["id"] == first["id"]
    assert repository.get_asset(first["id"], "b") is None


def test_publication_claim_requires_current_token(tmp_path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'claims.sqlite3').as_posix()}")
    repository = PreviewImageRepository(database)
    claim = repository.claim_publication("workspace", "a" * 64, lease_seconds=180)
    assert claim["status"] == "publishing"
    with pytest.raises(PreviewPublicationConflict):
        repository.mark_publication_succeeded(
            "workspace", "a" * 64, "wrong-token", "https://bucket.cos.ap-hongkong.myqcloud.com/a.jpg"
        )
```

Add `import pytest` at the top of the test module.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_images.py -q
```

Expected: imports for `preview_image_repository` fail.

- [ ] **Step 3: Define the three ORM rows and load their metadata**

Create `preview_image_orm.py` with these tables and exact state-bearing fields:

```python
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base, utc_now


class PreviewImageAssetRow(Base):
    __tablename__ = "product_processing_preview_image_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "task_id", "product_draft_id", "identity_hash",
            name="uq_preview_asset_workspace_task_draft_identity",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True)
    product_draft_id: Mapped[int] = mapped_column(ForeignKey("product_processing_drafts.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(32), index=True)
    source_asset_id: Mapped[str] = mapped_column(String(64), default="")
    identity_hash: Mapped[str] = mapped_column(String(64))
    managed_path: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    availability: Mapped[str] = mapped_column(String(32), default="local", index=True)
    public_url: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class PreviewImagePublicationRow(Base):
    __tablename__ = "product_processing_preview_publications"
    workspace_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    public_url: Mapped[str] = mapped_column(Text, default="")
    claim_token: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[str] = mapped_column(String(64), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class PreviewFinalizeRunRow(Base):
    __tablename__ = "product_processing_preview_finalize_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id", "snapshot_hash", name="uq_preview_finalize_snapshot"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    published_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    claim_token: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[str] = mapped_column(String(64), default="")
    workbook_path: Mapped[str] = mapped_column(Text, default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)
```

Import the module for metadata side effects in `database.py`:

```python
from . import preview_image_orm as _preview_image_orm  # noqa: F401
```

- [ ] **Step 4: Implement the repository's stable interface**

Create `preview_image_repository.py`. Use SQLite `sqlite_insert(Model).on_conflict_do_nothing()` for asset and run creation. Enforce `workspace_id` in every query. Implement the asset and publication methods with these concrete transaction shapes:

```python
class PreviewPublicationConflict(RuntimeError):
    pass


class PreviewRevisionConflict(RuntimeError):
    pass


class PreviewImageRepository:
    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def register_asset(self, *, workspace_id: str, task_id: int, product_draft_id: int,
                       origin: str, identity_hash: str, managed_path: str, source_url: str,
                       content_hash: str, content_type: str, byte_size: int, width: int,
                       height: int, source_asset_id: str = "") -> dict[str, Any]:
        asset_id = str(uuid5(
            NAMESPACE_URL,
            f"preview-asset:{workspace_id}:{task_id}:{product_draft_id}:{identity_hash}",
        ))
        values = {
            "id": asset_id, "workspace_id": workspace_id, "task_id": task_id,
            "product_draft_id": product_draft_id, "origin": origin,
            "source_asset_id": source_asset_id, "identity_hash": identity_hash,
            "managed_path": managed_path, "source_url": source_url,
            "content_hash": content_hash, "content_type": content_type,
            "byte_size": byte_size, "width": width, "height": height,
            "availability": "local" if managed_path else "materializing",
        }
        with self.database.sessions.begin() as session:
            session.execute(
                sqlite_insert(PreviewImageAssetRow).values(**values).on_conflict_do_nothing(
                    index_elements=["workspace_id", "task_id", "product_draft_id", "identity_hash"]
                )
            )
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.workspace_id == workspace_id,
                    PreviewImageAssetRow.task_id == task_id,
                    PreviewImageAssetRow.product_draft_id == product_draft_id,
                    PreviewImageAssetRow.identity_hash == identity_hash,
                )
            )
            if row is None:
                raise PreviewPublicationConflict("preview asset registration could not be loaded")
            return self._asset(row)

    def get_asset(self, asset_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == asset_id,
                    PreviewImageAssetRow.workspace_id == workspace_id,
                )
            )
            return self._asset(row) if row else None

    def list_assets(self, product_draft_id: int, workspace_id: str) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = session.scalars(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.product_draft_id == product_draft_id,
                    PreviewImageAssetRow.workspace_id == workspace_id,
                ).order_by(PreviewImageAssetRow.created_at, PreviewImageAssetRow.id)
            ).all()
            return [self._asset(row) for row in rows]

    def get_publication(self, workspace_id: str, content_hash: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.get(PreviewImagePublicationRow, (workspace_id, content_hash))
            return self._publication(row) if row else None

    def claim_publication(self, workspace_id: str, content_hash: str,
                          lease_seconds: int = 180) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
        token = uuid4().hex
        with self.database.sessions.begin() as session:
            session.execute(
                sqlite_insert(PreviewImagePublicationRow).values(
                    workspace_id=workspace_id, content_hash=content_hash, status="pending"
                ).on_conflict_do_nothing(index_elements=["workspace_id", "content_hash"])
            )
            claimed = session.execute(
                update(PreviewImagePublicationRow).where(
                    PreviewImagePublicationRow.workspace_id == workspace_id,
                    PreviewImagePublicationRow.content_hash == content_hash,
                    PreviewImagePublicationRow.status != "published",
                    or_(
                        PreviewImagePublicationRow.status != "publishing",
                        PreviewImagePublicationRow.claimed_at < cutoff,
                    ),
                ).values(
                    status="publishing", claim_token=token, claimed_at=now.isoformat(),
                    error_code="", error_message="", updated_at=utc_now(),
                )
            )
            row = session.get(PreviewImagePublicationRow, (workspace_id, content_hash))
            if row is None:
                raise PreviewPublicationConflict("publication row is missing")
            if row.status == "published":
                return self._publication(row)
            if claimed.rowcount != 1:
                raise PreviewPublicationConflict("preview image publication is already active")
            value = self._publication(row)
            value["claim_token"] = token
            return value

    def mark_publication_succeeded(self, workspace_id: str, content_hash: str,
                                   claim_token: str, public_url: str) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImagePublicationRow).where(
                    PreviewImagePublicationRow.workspace_id == workspace_id,
                    PreviewImagePublicationRow.content_hash == content_hash,
                    PreviewImagePublicationRow.status == "publishing",
                    PreviewImagePublicationRow.claim_token == claim_token,
                ).values(
                    status="published", public_url=public_url, claim_token="", claimed_at="",
                    error_code="", error_message="", updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict("preview publication claim changed")
            session.execute(
                update(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.workspace_id == workspace_id,
                    PreviewImageAssetRow.content_hash == content_hash,
                ).values(availability="published", public_url=public_url, updated_at=utc_now())
            )
            row = session.get(PreviewImagePublicationRow, (workspace_id, content_hash))
            return self._publication(row)

    def mark_publication_failed(self, workspace_id: str, content_hash: str,
                                claim_token: str, code: str, message: str) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImagePublicationRow).where(
                    PreviewImagePublicationRow.workspace_id == workspace_id,
                    PreviewImagePublicationRow.content_hash == content_hash,
                    PreviewImagePublicationRow.status == "publishing",
                    PreviewImagePublicationRow.claim_token == claim_token,
                ).values(
                    status="publish_failed", claim_token="", claimed_at="",
                    error_code=code, error_message=message[:240], updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict("preview publication claim changed")
            row = session.get(PreviewImagePublicationRow, (workspace_id, content_hash))
            return self._publication(row)
```

Import `datetime`, `timedelta`, `timezone`, `uuid4`, `uuid5`, `NAMESPACE_URL`, SQLAlchemy `and_`, `or_`, `select`, `update`, and SQLite `insert as sqlite_insert`. `_asset`, `_publication`, and `_run` serializers keep internal managed paths available to the service; only `PreviewImageService.public_asset()` removes them. Do not use process-local locks as the correctness boundary.

- [ ] **Step 5: Run tests and commit persistence**

Run the Task 2 pytest command. Expected: all preview image tests pass.

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_orm.py local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py local-runtime/wh_local/modules/product_processing/infrastructure/database.py local-runtime/tests/test_product_processing_preview_images.py
git commit -m "feat(product-processing): persist preview image assets"
```

## Task 3: Validate and store local preview images without COS

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_files.py`
- Create: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py:17-166`
- Modify: `local-runtime/wh_local/modules/product_processing/api/schemas.py:145-165`
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:364-414`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`

- [ ] **Step 1: Write failing local-upload tests**

Add tests using Pillow-generated bytes and a publisher spy:

```python
from io import BytesIO
from PIL import Image

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.preview_image_files import validate_preview_image
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.preview_image_service import PreviewImageService


def _jpeg_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), color).save(output, format="JPEG", quality=95)
    return output.getvalue()


def _owned_preview_service(database, assets, publisher=None):
    product_repository = ProductProcessingRepository(database)
    draft = product_repository.create_draft({
        "workspace_id": "workspace-a", "source_type": "manual",
        "product_name": "Owned product", "title": "Owned product",
    })
    task = product_repository.create_task(
        title="preview assets", preflight_only=False, settings={}, drafts=[draft],
        idempotency_key=None, workspace_id="workspace-a",
    )
    return (
        PreviewImageService(
            PreviewImageRepository(database), product_repository, assets,
            publisher=publisher,
        ),
        task["id"],
        draft["id"],
    )


def test_uploaded_asset_is_local_and_never_calls_publisher(tmp_path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'upload.sqlite3').as_posix()}")
    calls: list[str] = []
    service, task_id, draft_id = _owned_preview_service(
        database, ProductProcessingAssets(tmp_path / "assets"),
        lambda *_args: calls.append("called") or "https://unexpected.example/image.jpg",
    )
    asset = service.register_upload(
        task_id=task_id, product_draft_id=draft_id, workspace_id="workspace-a",
        filename="photo.jpg", content_type="image/jpeg", content=_jpeg_bytes(),
    )
    assert asset["origin"] == "upload"
    assert asset["publication_status"] == "local"
    assert asset["preview_url"].startswith("/pp-media/")
    assert "managed_path" not in asset
    assert calls == []


def test_animated_or_disguised_upload_is_rejected(tmp_path) -> None:
    output = BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        output, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")], duration=10,
    )
    with pytest.raises(ValueError, match="single-frame"):
        validate_preview_image(output.getvalue(), "image/jpeg")
```

- [ ] **Step 2: Run tests and confirm missing service/file validator**

Use the Task 2 pytest command. Expected: imports fail for the new modules.

- [ ] **Step 3: Implement strict decoding and content-addressed workspace storage**

`preview_image_files.py` must define:

```python
@dataclass(frozen=True)
class DecodedPreviewImage:
    content: bytes
    content_hash: str
    content_type: str
    suffix: str
    width: int
    height: int


def validate_preview_image(content: bytes, declared_content_type: str) -> DecodedPreviewImage:
    if not content or len(content) > 25 * 1024 * 1024:
        raise ValueError("preview image must be between 1 byte and 25 MiB")
    with Image.open(BytesIO(content)) as image:
        image.load()
        if int(getattr(image, "n_frames", 1)) != 1:
            raise ValueError("preview image must be single-frame")
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("preview image must be JPEG, PNG or WebP")
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > 40_000_000:
            raise ValueError("preview image exceeds the 40 MP limit")
        expected_type, suffix = {
            "JPEG": ("image/jpeg", ".jpg"),
            "PNG": ("image/png", ".png"),
            "WEBP": ("image/webp", ".webp"),
        }[image.format]
    declared = str(declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared != expected_type:
        raise ValueError("preview image content type does not match decoded bytes")
    return DecodedPreviewImage(
        content=bytes(content), content_hash=hashlib.sha256(content).hexdigest(),
        content_type=expected_type, suffix=suffix, width=width, height=height,
    )
```

Add `save_preview_asset()` and `require_workspace_preview_asset()` to `ProductProcessingAssets`. Store under:

```text
outputs/preview-assets/workspaces/<sha256(workspace_id)[:24]>/<content_hash[:2]>/<content_hash><suffix>
```

Use the same resolved-parent containment checks as `save_dimension_asset`; write only when the content-addressed file does not already exist.

- [ ] **Step 4: Implement local registration and replace the upload route**

Start `PreviewImageService` with this constructor and public DTO boundary:

```python
class PreviewImageService:
    def __init__(self, repository: PreviewImageRepository,
                 product_repository: ProductProcessingRepository,
                 assets: ProductProcessingAssets,
                 publisher: Callable[[bytes, str, str, str, str], str] | None = None,
                 trusted_public_url: Callable[[str], bool] | None = None,
                 public_image_fetcher: Callable[[str], FetchedPublicImage] = fetch_public_image,
                 max_publish_workers: int = 4):
        if not 1 <= max_publish_workers <= 6:
            raise ValueError("preview publish workers must be between 1 and 6")
        self.repository = repository
        self.product_repository = product_repository
        self.assets = assets
        self.publisher = publisher
        self.trusted_public_url = trusted_public_url or (lambda _value: False)
        self.public_image_fetcher = public_image_fetcher
        self.max_publish_workers = max_publish_workers

    def require_task_draft(self, task_id: int, product_draft_id: int,
                           workspace_id: str) -> None:
        task = self.product_repository.get_task(task_id, workspace_id)
        owned_drafts = {
            int(item["product_draft_id"])
            for item in (task or {}).get("items", [])
            if item.get("product_draft_id") is not None
        }
        if task is None or product_draft_id not in owned_drafts:
            raise LookupError("preview image target does not belong to this task")

    def register_upload(self, *, task_id: int, product_draft_id: int, workspace_id: str,
                        filename: str, content_type: str, content: bytes) -> dict[str, Any]:
        self.require_task_draft(task_id, product_draft_id, workspace_id)
        decoded = validate_preview_image(content, content_type)
        path = self.assets.save_preview_asset(
            decoded.content, decoded.content_hash, decoded.suffix, workspace_id=workspace_id
        )
        asset = self.repository.register_asset(
            workspace_id=workspace_id, task_id=task_id, product_draft_id=product_draft_id,
            origin="upload", identity_hash=decoded.content_hash, managed_path=str(path),
            source_url="", content_hash=decoded.content_hash,
            content_type=decoded.content_type, byte_size=len(decoded.content),
            width=decoded.width, height=decoded.height,
        )
        return self.public_asset(asset)
```

`public_asset()` returns only `id`, `origin`, `preview_url`, `publication_status`, `public_url`, `width`, and `height`. Replace `/preview/images` with `/preview/assets`:

```python
@router.post("/tasks/{task_id}/preview/assets")
async def upload_preview_assets(
    task_id: int,
    request: Request,
    workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
) -> dict[str, Any]:
    form = await request.form()
    draft_id = int(str(form.get("draft_id") or 0) or 0)
    files = [value for value in form.getlist("image_files") if hasattr(value, "read")]
    if draft_id <= 0 or not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "draft_id and image_files are required")
    _call(service.require_preview_target, task_id, draft_id,
          workspace_id=_workspace(workspace_id))
    assets: list[dict[str, Any]] = []
    try:
        for upload in files:
            assets.append(_call(
                service.register_preview_upload,
                task_id,
                draft_id,
                await upload.read(),
                _filename(upload, "preview-image.jpg"),
                str(getattr(upload, "content_type", "") or ""),
                workspace_id=_workspace(workspace_id),
            ))
    finally:
        for upload in files:
            await upload.close()
    return {"assets": assets}
```

Add this delegate; the service ownership check runs before decoding or saving each image:

```python
def require_preview_target(self, task_id: int, draft_id: int, *,
                           workspace_id: str = "local") -> None:
    try:
        self.preview_images.require_task_draft(task_id, draft_id, workspace_id)
    except LookupError as exc:
        raise ProductProcessingNotFound(str(exc)) from exc

def register_preview_upload(self, task_id: int, draft_id: int, content: bytes,
                            filename: str, content_type: str, *,
                            workspace_id: str = "local") -> dict[str, Any]:
    try:
        return self.preview_images.register_upload(
            task_id=task_id, product_draft_id=draft_id, workspace_id=workspace_id,
            filename=filename, content_type=content_type, content=content,
        )
    except LookupError as exc:
        raise ProductProcessingNotFound(str(exc)) from exc
```

- [ ] **Step 5: Run focused tests and commit local uploads**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_dimension_schema.py -q
```

Expected: all selected tests pass.

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_files.py local-runtime/wh_local/modules/product_processing/preview_image_service.py local-runtime/wh_local/modules/product_processing/infrastructure/assets.py local-runtime/wh_local/modules/product_processing/api/schemas.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/tests/test_product_processing_preview_images.py
git commit -m "feat(product-processing): register preview uploads locally"
```

## Task 4: Project legacy images and save revision-safe manifests

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py:177-205`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1100-1380`
- Modify: `local-runtime/wh_local/modules/product_processing/api/schemas.py:145-165`
- Modify: `local-runtime/tests/test_product_processing_preview_overrides.py`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`

- [ ] **Step 1: Replace empty-list and stale-write expectations with failing v2 tests**

Add these contracts, reusing the existing `_service()` and `_create_task_with_result()` helpers in `test_product_processing_preview_overrides.py`:

```python
def test_preview_projection_is_idempotent_and_returns_asset_ids(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    first = service.task_preview(task["id"], workspace_id="local")["items"][0]
    second = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert first["image_manifest"] == second["image_manifest"]
    assert first["assets"] == second["assets"]
    assert first["image_manifest"]["main_asset_id"]


def test_explicit_empty_detail_and_carousel_lists_survive_save_reload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    item = service.task_preview(task["id"], workspace_id="local")["items"][0]
    saved = service.save_task_preview(
        task["id"],
        [{
            "product_draft_id": item["product_draft_id"],
            "expected_preview_revision": item["preview_revision"],
            "overrides": {
                "image_manifest_v2": {
                    "main_asset_id": "",
                    "carousel_asset_ids": [],
                    "detail_asset_ids": [],
                }
            },
        }],
        workspace_id="local",
    )
    assert saved["saved_count"] == 1
    reloaded = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert reloaded["image_manifest"]["carousel_asset_ids"] == []
    assert reloaded["image_manifest"]["detail_asset_ids"] == []


def test_stale_preview_manifest_save_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    item = service.task_preview(task["id"], workspace_id="local")["items"][0]
    service.repository.save_draft_preview_overrides(
        item["product_draft_id"], {"title": "newer"}, expected_revision=item["preview_revision"]
    )
    with pytest.raises(ProductProcessingConflict, match="revision"):
        service.save_task_preview(
            task["id"],
            [{"product_draft_id": item["product_draft_id"],
              "expected_preview_revision": item["preview_revision"], "overrides": {}}],
            workspace_id="local",
        )


def test_later_image_save_preserves_previously_saved_text(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    first = service.task_preview(task["id"], workspace_id="local")["items"][0]
    service.save_task_preview(task["id"], [{
        "product_draft_id": first["product_draft_id"],
        "expected_preview_revision": first["preview_revision"],
        "overrides": {
            "title": "Saved title",
            "description": first["description"],
            "core_fields": first["core_fields"],
            "image_manifest_v2": first["image_manifest"],
        },
    }], workspace_id="local")
    second = service.task_preview(task["id"], workspace_id="local")["items"][0]
    service.save_task_preview(task["id"], [{
        "product_draft_id": second["product_draft_id"],
        "expected_preview_revision": second["preview_revision"],
        "overrides": {
            "title": second["title"],
            "description": second["description"],
            "core_fields": second["core_fields"],
            "image_manifest_v2": {**second["image_manifest"], "detail_asset_ids": []},
        },
    }], workspace_id="local")
    reloaded = service.task_preview(task["id"], workspace_id="local")["items"][0]
    assert reloaded["title"] == "Saved title"
    assert reloaded["image_manifest"]["detail_asset_ids"] == []
```

- [ ] **Step 2: Run focused tests and confirm current URL-array behavior fails**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_overrides.py local-runtime/tests/test_product_processing_preview_images.py -q
```

Expected: new asset/manifest fields are absent and explicit empty lists do not survive.

- [ ] **Step 3: Implement idempotent legacy projection**

Add `project_item_images()` to `PreviewImageService`. It must:

1. Prefer saved `image_manifest_v2` when the key is present, including empty arrays.
2. Otherwise read semantic `image_manifest`, `carousel_image_paths`, `grid_image_summary_path`, `detail_image_paths`, `main_image`, and legacy preview overrides. Register the four-grid summary as the final carousel asset so it can be deleted, reordered and published like every other generated image.
3. Register each value with an identity hash of `sha256("remote:" + normalized_url)` or `sha256("managed:" + resolved_managed_path)`.
4. Assign `origin=source` for source URLs, `origin=dimension` for accepted dimension references, and `origin=generated` for generated result fields.
5. Return `assets`, `image_manifest`, and URL compatibility fields resolved from the same manifest.

The response shape must be:

```python
{
    "assets": [self.public_asset(asset) for asset in assets],
    "image_manifest": manifest.as_dict(),
    "main_image": preview_by_id.get(manifest.main_asset_id, ""),
    "carousel_images": [preview_by_id[asset_id] for asset_id in manifest.carousel_asset_ids],
    "detail_images": [preview_by_id[asset_id] for asset_id in manifest.detail_asset_ids],
    "exportable": bool(result.get("optimized_title")),
}
```

- [ ] **Step 4: Make preview saves revision-safe and preserve empty manifests**

Require `expected_preview_revision` in `PreviewSaveItem`. The new client submits the complete desired precheck state (`title`, `description`, full `core_fields`, and `image_manifest_v2`) on every save/finalize; it does not send a partial patch that could erase older saved fields. In `save_task_preview`, call `save_draft_preview_overrides(..., expected_revision=...)` and translate `StalePreviewRevision` into `ProductProcessingConflict`. `_clean_preview_overrides()` must retain `image_manifest_v2` whenever the key is present:

```python
if MANIFEST_KEY in overrides:
    cleaned[MANIFEST_KEY] = PreviewImageManifest.from_value(
        overrides.get(MANIFEST_KEY)
    ).as_dict()
```

Stop treating an empty image list as “no override.” Keep legacy URL fields read-compatible, but new frontend writes must use only `image_manifest_v2`.

- [ ] **Step 5: Run focused tests and commit projection/save**

Run the Task 4 pytest command. Expected: all selected tests pass.

```powershell
git add local-runtime/wh_local/modules/product_processing/preview_image_service.py local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py local-runtime/wh_local/modules/product_processing/infrastructure/repository.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/wh_local/modules/product_processing/api/schemas.py local-runtime/tests/test_product_processing_preview_overrides.py local-runtime/tests/test_product_processing_preview_images.py
git commit -m "feat(product-processing): save versioned preview image manifests"
```

## Task 5: Persist AI-generated images locally instead of publishing early

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:176-186, 1930-2030, 2380-2584, 3336-3376`
- Modify: `local-runtime/tests/test_product_processing_image_quality.py`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`

- [ ] **Step 1: Write a failing no-early-COS generation test**

Use a real `GeneratedMedia` and a processor whose COS method raises if called:

```python
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia


def test_generated_media_is_registered_local_without_cos(tmp_path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'generated.sqlite3').as_posix()}")
    preview, task_id, draft_id = _owned_preview_service(
        database, ProductProcessingAssets(tmp_path / "assets")
    )
    media = GeneratedMedia(
        stage="grid_image_1", content=_jpeg_bytes("green"), content_type="image/jpeg",
        suffix=".jpg", provider="test", model="test", reference_count=1,
    )
    asset = preview.register_generated(
        task_id=task_id, product_draft_id=draft_id, workspace_id="workspace-a", media=media
    )
    assert asset["origin"] == "generated"
    assert asset["publication_status"] == "local"
    assert asset["preview_url"].startswith("/pp-media/")
```

Add an integration test around `_generate_detail_images_local()` with a fake media processor whose `upload_to_cos()` raises `AssertionError("early COS call")`; assert the method returns a `/pp-media/` preview value and the assertion is never raised.

- [ ] **Step 2: Run tests and confirm generated registration is missing**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_image_quality.py -q
```

Expected: `register_generated` is missing or current generation invokes `_publish_media`.

- [ ] **Step 3: Implement generated-asset registration**

Add this method to `PreviewImageService`:

```python
def register_generated(self, *, task_id: int, product_draft_id: int,
                       workspace_id: str, media: GeneratedMedia) -> dict[str, Any]:
    self.require_task_draft(task_id, product_draft_id, workspace_id)
    decoded = validate_preview_image(media.content, media.content_type)
    path = self.assets.save_preview_asset(
        decoded.content, decoded.content_hash, decoded.suffix, workspace_id=workspace_id
    )
    asset = self.repository.register_asset(
        workspace_id=workspace_id, task_id=task_id, product_draft_id=product_draft_id,
        origin="generated", identity_hash=decoded.content_hash, managed_path=str(path),
        source_url="", content_hash=decoded.content_hash,
        content_type=decoded.content_type, byte_size=len(decoded.content),
        width=decoded.width, height=decoded.height,
    )
    return self.public_asset(asset)
```

Construct one `PreviewImageService` in `ProductProcessingService.__init__` from the shared database and assets. Registration methods never call the injected publisher:

```python
self.preview_images = PreviewImageService(
    PreviewImageRepository(repository.database), repository, assets,
    publisher=None,
    public_image_fetcher=public_image_fetcher,
    max_publish_workers=4,
)
```

- [ ] **Step 4: Replace `_publish_media` with local persistence at all three call sites**

Rename the helper to `_persist_media_for_preview`:

```python
def _persist_media_for_preview(self, parts: list[Any], task_id: int, draft_id: int,
                               workspace_id: str) -> list[str]:
    values: list[str] = []
    for part in parts:
        asset = self.preview_images.register_generated(
            task_id=task_id, product_draft_id=draft_id,
            workspace_id=workspace_id, media=part,
        )
        values.append(str(asset["preview_url"]))
    return values
```

Thread `workspace_id` through `_generate_grid_images`, `_generate_detail_images`, and `_generate_detail_images_local`, then replace all `_publish_media(...)` calls. Rename timing key `publish_ms` to `persist_ms`. Remove “COS unconfigured” notes from generation because COS is intentionally deferred. Keep generated bytes unchanged; do not introduce another JPEG encode.

- [ ] **Step 5: Run focused tests and commit local generation**

Run the Task 5 pytest command. Expected: selected tests pass and publisher call count remains zero.

```powershell
git add local-runtime/wh_local/modules/product_processing/preview_image_service.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_image_quality.py
git commit -m "feat(product-processing): defer generated image publication"
```

## Task 6: Hand dimension images back as local preview assets

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py:30-64, 283-320, 480-635`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py:757-1010, 1120-1170`
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:45-68`
- Modify: `local-runtime/tests/test_product_processing_dimension_canvas.py`
- Modify: `local-runtime/tests/test_product_processing_preview_overrides.py`

- [ ] **Step 1: Change the dimension tests to require zero COS calls**

Update the submit/accept test contract:

```python
def test_dimension_submit_and_accept_register_local_preview_asset(dimension_fixture) -> None:
    service, product_repository, publisher_calls, item = dimension_fixture
    completed = dimension_fixture.complete(item)
    change_set = service.submit_review(completed["batch_id"], workspace_id="workspace-a")
    assert publisher_calls == []
    change_item = change_set["items"][0]
    assert change_item["new_image_url"].startswith("/pp-media/")

    accepted = service.accept_change_item(
        change_set["id"], change_item["id"], workspace_id="workspace-a"
    )
    assert accepted["status"] == "accepted"
    draft = product_repository.get_draft(item["product_draft_id"], workspace_id="workspace-a")
    manifest = draft["preview_overrides"]["image_manifest_v2"]
    assert manifest["carousel_asset_ids"][3]
    assert publisher_calls == []
```

Retain existing conflict assertions for source preview revision, target slot identity, physical dimensions and stale change items.

- [ ] **Step 2: Run dimension tests and confirm current submit publishes**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_preview_overrides.py -q
```

Expected: publisher call assertion fails and acceptance rejects a local render.

- [ ] **Step 3: Remove publication from dimension review submission**

In `DimensionCanvasService.submit_review`, delete `_publish_render_asset()` from the loop. Build identity from the immutable local render:

```python
asset = self.canvas_repository.get_asset(item["render_asset_id"], item["id"], workspace_id)
if asset is None or asset.get("availability") not in {"local", "published"}:
    raise DimensionCanvasConflict("rendered asset not found")
identities.append(
    f"{item['id']}:{item['render_revision']}:{asset.get('content_hash') or asset['id']}"
)
```

Remove the publisher constructor parameter and `_publish_render_asset`; keep `_asset_preview_url()` so review uses `/pp-media/`. Stop passing `publisher=service.publish_dimension_media` from `api/router.py`.

- [ ] **Step 4: Accept the managed render into the preview manifest atomically**

In `create_change_set`, require `replacement.role == "rendered_dimension"`, a non-empty managed path and content hash; do not require `availability=published` or an HTTPS source URL.

In `accept_change_item`, while holding the existing database transaction:

1. Validate the dimension asset belongs to the workspace and item.
2. Insert or read a `PreviewImageAssetRow` with deterministic identity `sha256("dimension:" + managed_asset.id)` and `source_asset_id=managed_asset.id`.
3. Parse the current `image_manifest_v2`; if absent, project the legacy carousel into preview assets within the same transaction.
4. Call `replace_carousel_slot(manifest, change_item.target_slot_id, preview_asset.id)`.
5. Store the resulting manifest and preserve unrelated title, description, fields and details.
6. Increment `preview_revision` with the existing compare-and-swap.

The dimension asset remains local and the change-set DTO uses `_asset_preview_url()` for `new_image_url`. No public URL is written to preview overrides at this stage.

- [ ] **Step 5: Run tests and commit dimension handoff**

Run the Task 6 pytest command. Expected: all selected tests pass with zero publisher calls.

```powershell
git add local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_preview_overrides.py
git commit -m "feat(product-processing): keep dimension review images local"
```

## Task 7: Generalize deterministic COS publication for final preview assets

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py:369-452`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:669-710, 3240-3335`
- Modify: `local-runtime/tests/test_product_processing_image_quality.py`

- [ ] **Step 1: Extend the existing content-addressed COS test**

Change the test to publish into a supplied collection and verify the exact key and HEAD reuse:

```python
first = processor.upload_content_addressed_to_cos(
    media, namespace="workspace-a", content_hash=digest, collection="preview-final"
)
second = processor.upload_content_addressed_to_cos(
    media, namespace="workspace-a", content_hash=digest, collection="preview-final"
)
assert first == second
assert client.put_keys == [
    f"product-processing/preview-final/workspace-a/{digest[:2]}/{digest}.jpg"
]
assert client.head_keys.count(client.put_keys[0]) >= 2
```

Add a test where `put_object` raises after recording the object and the reconciliation HEAD succeeds; the method must return the same URL.

Add trusted legacy COS recognition:

```python
assert processor.is_configured_cos_url(
    "https://bucket.cos.ap-hongkong.myqcloud.com/product-processing/old.jpg"
) is True
assert processor.is_configured_cos_url("https://other.example.com/old.jpg") is False
```

The fake COS client must record one `head_object` for the matching configured bucket/key and return success.

- [ ] **Step 2: Run the exact COS tests and confirm the collection argument fails**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_image_quality.py -q
```

Expected: `upload_content_addressed_to_cos()` rejects `collection`.

- [ ] **Step 3: Add the sanitized collection segment without changing dimension keys by default**

Change the signature to:

```python
def upload_content_addressed_to_cos(self, media: GeneratedMedia, *, namespace: str,
                                    content_hash: str,
                                    collection: str = "dimension-canvas") -> str:
```

Sanitize both collection and namespace with the existing restricted-character expression. Build:

```python
key = "/".join(
    part for part in (
        prefix or "product-processing",
        safe_collection or "preview-final",
        safe_namespace or "workspace",
        digest[:2],
        f"{digest}{suffix}",
    ) if part
)
```

Keep the current HEAD-before-PUT and HEAD-after-uncertain-failure behavior unchanged.

Add `is_configured_cos_url(url)` to parse the URL, require HTTPS, require hostname exactly `<bucket>.cos.<region>.myqcloud.com`, require a non-empty object key, and confirm it with `head_object(Bucket=bucket, Key=key)`. Return `False` for a hostname mismatch or 404; raise a sanitized `MediaProcessingError` for other COS errors.

- [ ] **Step 4: Add the preview publisher adapter**

Replace dimension-specific publication coupling with a generic adapter in `ProductProcessingService`:

```python
def publish_preview_media(self, content: bytes, content_type: str, suffix: str,
                          content_hash: str, workspace_id: str) -> str:
    from .infrastructure.media import GeneratedMedia
    digest = hashlib.sha256(content).hexdigest()
    if digest != str(content_hash or "").strip().lower():
        raise ValueError("preview image hash mismatch")
    namespace = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:20]
    media = GeneratedMedia(
        stage="preview-final", content=content, content_type=content_type,
        suffix=suffix, provider="preview-finalizer", model="original-bytes",
        reference_count=0,
    )
    url = self._media_processor().upload_content_addressed_to_cos(
        media, namespace=namespace, content_hash=digest, collection="preview-final"
    )
    if not url.lower().startswith("https://") or not is_safe_external_url(url):
        raise ValueError("COS returned a non-public preview image URL")
    return url
```

Add `ProductProcessingService.is_trusted_cos_url(value)` as a thin call to `self._media_processor().is_configured_cos_url(value)`. Wire both `publish_preview_media` and `is_trusted_cos_url` into `PreviewImageService`. Do not fall back to `WH_MEDIA_BASE_URL` or a managed path.

Delete the now-unused `publish_dimension_media` adapter after dimension tests have moved to local review handoff. There must be only one final-image COS write path: `publish_preview_media` invoked by the finalizer.

- [ ] **Step 5: Run tests and commit the publisher**

Run the Task 7 pytest command. Expected: all image quality tests pass.

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/media.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_image_quality.py
git commit -m "feat(product-processing): publish final preview images by hash"
```

## Task 8: Build idempotent background finalization and workbook gating

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1080-1270`
- Modify: `local-runtime/wh_local/modules/product_processing/domain/workbooks.py:60-75, 180-205`
- Create: `local-runtime/tests/test_product_processing_preview_finalize.py`

- [ ] **Step 1: Write finalization failure, retry, and deduplication tests**

Build a fixture with two drafts, locally registered JPEG assets, a publisher that records content hashes, and `trusted_public_url=lambda value: value.startswith("https://test-bucket.cos.test/")`. Return `(service, task_id, items, publisher, meta)`, where `meta` contains `removed_content_hash` and `main_content_hash`. The tests call a synchronous `run_finalize()` seam after `begin_finalize()`:

Import `datetime`, `timedelta`, `timezone`, SQLAlchemy `update`, and `PreviewFinalizeRunRow` for the lease test.

```python
def test_finalize_publishes_only_live_unique_assets_and_writes_workbook(finalize_fixture) -> None:
    service, task_id, items, publisher, _meta = finalize_fixture
    run = service.begin_finalize(task_id, items, workspace_id="local", launch=False)
    completed = service.run_finalize(run["id"], workspace_id="local")
    assert completed["status"] == "completed"
    assert len(publisher.calls) == len(set(publisher.calls))
    assert completed["published_count"] == completed["total_count"]
    assert completed["download"].endswith("kind=dxm_final")


def test_deleted_asset_is_not_published(finalize_fixture) -> None:
    service, task_id, items, publisher, meta = finalize_fixture
    removed_hash = meta["removed_content_hash"]
    run = service.begin_finalize(task_id, items, workspace_id="local", launch=False)
    service.run_finalize(run["id"], workspace_id="local")
    assert removed_hash not in publisher.calls


def test_publish_failure_blocks_workbook_and_retry_only_calls_failed_hash(finalize_fixture) -> None:
    service, task_id, items, publisher, meta = finalize_fixture
    publisher.fail_once_for = meta["main_content_hash"]
    run = service.begin_finalize(task_id, items, workspace_id="local", launch=False)
    failed = service.run_finalize(run["id"], workspace_id="local")
    assert failed["status"] == "publish_failed"
    assert failed["workbook_ready"] is False
    successful_before = set(publisher.successful_hashes)
    retried = service.retry_finalize(run["id"], workspace_id="local", launch=False)
    completed = service.run_finalize(retried["id"], workspace_id="local")
    assert completed["status"] == "completed"
    assert successful_before <= set(publisher.successful_hashes)
    assert publisher.call_counts[meta["main_content_hash"]] == 2


def test_revision_change_before_workbook_marks_run_stale(finalize_fixture) -> None:
    service, task_id, items, _publisher, _meta = finalize_fixture
    run = service.begin_finalize(task_id, items, workspace_id="local", launch=False)
    service.product_repository.save_draft_preview_overrides(
        items[0]["product_draft_id"], {"title": "newer"},
        expected_revision=run["snapshot"][0]["preview_revision"], workspace_id="local",
    )
    result = service.run_finalize(run["id"], workspace_id="local")
    assert result["status"] == "stale"
    assert result["workbook_ready"] is False


def test_active_finalize_run_cannot_be_reclaimed_and_old_token_cannot_finish(finalize_fixture) -> None:
    service, task_id, items, _publisher, _meta = finalize_fixture
    run = service.begin_finalize(task_id, items, workspace_id="local", launch=False)
    first = service.repository.claim_finalize_run(run["id"], "local", lease_seconds=180)
    with pytest.raises(PreviewPublicationConflict):
        service.repository.claim_finalize_run(run["id"], "local", lease_seconds=180)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
    with service.repository.database.sessions.begin() as session:
        session.execute(update(PreviewFinalizeRunRow).where(
            PreviewFinalizeRunRow.id == run["id"],
            PreviewFinalizeRunRow.workspace_id == "local",
        ).values(claimed_at=expired))
    second = service.repository.claim_finalize_run(run["id"], "local", lease_seconds=180)
    assert second["claim_token"] != first["claim_token"]
    with pytest.raises(PreviewPublicationConflict):
        service.repository.mark_finalize_completed(
            run["id"], "local", first["claim_token"],
            workbook_path="C:/stale.xlsx", row_count=1, product_count=1,
        )
```

- [ ] **Step 2: Run the new tests and confirm finalization methods are missing**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_finalize.py -q
```

Expected: collection or attribute failures for finalization methods.

- [ ] **Step 3: Implement atomic save-and-snapshot creation**

Add `create_finalize_run()` to `PreviewImageRepository`. In one `sessions.begin()` transaction it must:

- Lock logically through compare-and-swap on every supplied `ProductDraftRow.preview_revision`.
- Derive the task's exportable draft IDs from successful task results and require the request to contain every exportable draft exactly once; failed/non-exportable task items do not require a main image and are excluded from the snapshot.
- Normalize and save each override, including explicit empty manifests.
- Validate every manifest asset belongs to the same workspace, task and draft.
- Reject a missing `main_asset_id` or a main asset not present in `carousel_asset_ids`; main and product-material image must reference one retained carousel asset.
- Record the post-save revision for each draft.
- Compute `snapshot_hash()` over sorted draft entries containing draft ID, revision, overrides and live asset IDs.
- Insert `PreviewFinalizeRunRow` with SQLite `ON CONFLICT DO NOTHING`, then read and return the existing or inserted run.
- Provide `claim_finalize_run(run_id: str, workspace_id: str, lease_seconds: int = 180)`, `renew_finalize_claim(run_id: str, workspace_id: str, claim_token: str)`, `mark_finalize_failed(run_id: str, workspace_id: str, claim_token: str, errors: list[dict[str, Any]])`, `mark_finalize_stale(run_id: str, workspace_id: str, claim_token: str)`, and `mark_finalize_completed(run_id: str, workspace_id: str, claim_token: str, workbook_path: str, row_count: int, product_count: int)`. Every mutation after claim requires the current run token; a second worker may reclaim only when `claimed_at` is older than the lease cutoff. Tests expire a lease by updating the test database directly; no test-only production method is added.

Use this compare-and-swap pattern for run ownership:

```python
def claim_finalize_run(self, run_id: str, workspace_id: str,
                       lease_seconds: int = 180) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
    token = uuid4().hex
    with self.database.sessions.begin() as session:
        claimed = session.execute(
            update(PreviewFinalizeRunRow).where(
                PreviewFinalizeRunRow.id == run_id,
                PreviewFinalizeRunRow.workspace_id == workspace_id,
                or_(
                    PreviewFinalizeRunRow.status.in_(["queued", "publish_failed"]),
                    and_(
                        PreviewFinalizeRunRow.status == "publishing",
                        PreviewFinalizeRunRow.claimed_at < cutoff,
                    ),
                ),
            ).values(status="publishing", claim_token=token,
                     claimed_at=now.isoformat(), updated_at=utc_now())
        )
        row = session.scalar(select(PreviewFinalizeRunRow).where(
            PreviewFinalizeRunRow.id == run_id,
            PreviewFinalizeRunRow.workspace_id == workspace_id,
        ))
        if row is None:
            raise LookupError("preview finalization run not found")
        if claimed.rowcount != 1:
            if row.status in {"completed", "stale"}:
                return self._run(row)
            raise PreviewPublicationConflict("preview finalization is already active")
        value = self._run(row)
        value["claim_token"] = token
        return value


def renew_finalize_claim(self, run_id: str, workspace_id: str,
                         claim_token: str) -> None:
    with self.database.sessions.begin() as session:
        changed = session.execute(update(PreviewFinalizeRunRow).where(
            PreviewFinalizeRunRow.id == run_id,
            PreviewFinalizeRunRow.workspace_id == workspace_id,
            PreviewFinalizeRunRow.status == "publishing",
            PreviewFinalizeRunRow.claim_token == claim_token,
        ).values(claimed_at=utc_now(), updated_at=utc_now()))
        if changed.rowcount != 1:
            raise PreviewPublicationConflict("preview finalization claim changed")


def _finish_finalize_run(self, run_id: str, workspace_id: str, claim_token: str,
                         status: str, values: dict[str, Any]) -> dict[str, Any]:
    with self.database.sessions.begin() as session:
        changed = session.execute(update(PreviewFinalizeRunRow).where(
            PreviewFinalizeRunRow.id == run_id,
            PreviewFinalizeRunRow.workspace_id == workspace_id,
            PreviewFinalizeRunRow.status == "publishing",
            PreviewFinalizeRunRow.claim_token == claim_token,
        ).values(**values, status=status, claim_token="", claimed_at="", updated_at=utc_now()))
        if changed.rowcount != 1:
            raise PreviewPublicationConflict("preview finalization claim changed")
        row = session.scalar(select(PreviewFinalizeRunRow).where(
            PreviewFinalizeRunRow.id == run_id,
            PreviewFinalizeRunRow.workspace_id == workspace_id,
        ))
        return self._run(row)
```

`mark_finalize_failed`, `mark_finalize_stale`, and `mark_finalize_completed` call `_finish_finalize_run` with explicit counts/errors or workbook fields. None accepts a missing token.

Use a snapshot entry shaped exactly as:

```python
{
    "product_draft_id": draft.id,
    "preview_revision": next_revision,
    "overrides": normalized_overrides,
    "manifest": manifest.as_dict(),
    "live_asset_ids": list(manifest.live_asset_ids()),
}
```

Also add a token-free legacy reuse method that is only called after the configured COS HEAD check:

```python
def mark_asset_reused_public_url(self, asset_id: str, workspace_id: str,
                                 public_url: str) -> dict[str, Any]:
    with self.database.sessions.begin() as session:
        changed = session.execute(
            update(PreviewImageAssetRow).where(
                PreviewImageAssetRow.id == asset_id,
                PreviewImageAssetRow.workspace_id == workspace_id,
            ).values(availability="published", public_url=public_url,
                     error_code="", error_message="", updated_at=utc_now())
        )
        if changed.rowcount != 1:
            raise LookupError("preview image asset not found")
        row = session.scalar(select(PreviewImageAssetRow).where(
            PreviewImageAssetRow.id == asset_id,
            PreviewImageAssetRow.workspace_id == workspace_id,
        ))
        return self._asset(row)
```

- [ ] **Step 4: Implement materialization, publication, and final workbook creation**

Add these methods to `PreviewImageService`:

```python
def begin_finalize(self, task_id: int, items: list[dict[str, Any]], *,
                   workspace_id: str, launch: bool = True) -> dict[str, Any]
def get_finalize(self, run_id: str, *, workspace_id: str) -> dict[str, Any]
def retry_finalize(self, run_id: str, *, workspace_id: str,
                   launch: bool = True) -> dict[str, Any]
def run_finalize(self, run_id: str, *, workspace_id: str) -> dict[str, Any]
def finalize_download_path(self, run_id: str, task_id: int, *,
                           workspace_id: str) -> Path
```

`run_finalize()` performs these exact phases:

1. Atomically claim a `queued` or `publish_failed` run with a random run token and 180-second lease; return completed/stale runs unchanged and refuse an active second claim.
2. Reuse an asset already carrying a persisted trusted public URL. For legacy `source_url`, call `trusted_public_url`; on a successful configured-COS HEAD, atomically mark that workspace asset `published` without downloading or PUT.
3. Materialize every other remote source with the injected SSRF-safe fetcher and `validate_preview_image`; update its managed path and content hash.
4. Deduplicate by content hash and publish with `ThreadPoolExecutor(max_workers=self.max_publish_workers)`.
5. For each hash, claim the workspace publication receipt. Reuse `published`; when another worker owns a live claim, call `get_publication()` with bounded 0.25–2 second backoff until it becomes `published`/`publish_failed` or its 180-second lease expires, then reclaim only the expired lease.
6. Read bytes only through `require_workspace_preview_asset`, call the injected publisher, require safe HTTPS and `trusted_public_url(result) == True`, then token-check success/failure.
7. Renew the run lease after each materialization/publication result. If any hash failed, token-check and persist `publish_failed`, per-asset errors and counts; do not create a workbook.
8. Recheck every draft revision from the snapshot. Any mismatch token-checks and marks the run `stale`; it does not create a workbook.
9. Resolve manifest IDs into transient `main_image`, `carousel_images`, and `detail_images` public URL overrides. Remove legacy `image_slot_overrides` from that transient export copy so it cannot take precedence over the finalized manifest; do not modify the stored draft overrides.
10. Call `create_result_workbook()` into `task_<id>/finalizations/<run_id>/dxm_import_task_<id>_final.xlsx` and token-check the final `completed` update. An expired old worker cannot complete or release a newer claim.

Launch production runs in a named daemon thread `pp-preview-finalize-<run_id>`. On status lookup, recover a run left `publishing` only after its run lease has expired; publication receipts still obey their own leases. Never clear active claims at service construction.

- [ ] **Step 5: Harden workbook image input and commit finalization**

Keep `_http_urls()` as defense in depth, but add an explicit resolver assertion before workbook creation:

```python
def require_final_public_image_urls(values: list[str]) -> list[str]:
    normalized = [str(value or "").strip() for value in values]
    if any(not value.lower().startswith("https://") or not is_safe_external_url(value)
           for value in normalized):
        raise ValueError("final workbook images must be public HTTPS URLs")
    return normalized
```

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_finalize.py local-runtime/tests/test_product_processing_preview_overrides.py local-runtime/tests/test_product_processing_image_slots.py -q
```

Expected: all selected tests pass and failed runs create no workbook.

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py local-runtime/wh_local/modules/product_processing/preview_image_service.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/wh_local/modules/product_processing/domain/workbooks.py local-runtime/tests/test_product_processing_preview_finalize.py local-runtime/tests/test_product_processing_preview_overrides.py
git commit -m "feat(product-processing): finalize preview images before export"
```

## Task 9: Expose revision-safe asset and async finalization APIs

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/api/schemas.py:145-175`
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:364-430`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1100-1265`
- Modify: `local-runtime/tests/test_product_processing_preview_finalize.py`

- [ ] **Step 1: Add failing FastAPI contract tests**

Create a `TestClient` with `create_product_processing_router(service)`. The fixture injects a fake publisher and monkeypatches `service.engine_status()` so only `diagnostics.config.cos_configured` is `True`; no real COS client is constructed. Assert:

```python
def test_finalize_api_returns_run_and_progress(api_fixture) -> None:
    client, task_id, request_body = api_fixture
    started = client.post(
        f"/product-processing/tasks/{task_id}/preview/finalize",
        headers={"X-Workspace-ID": "workspace-a", "Idempotency-Key": "finalize-1"},
        json=request_body,
    )
    assert started.status_code == 202
    run = started.json()
    assert run["status"] in {"queued", "publishing"}
    status_response = client.get(
        f"/product-processing/tasks/{task_id}/preview/finalize/{run['id']}",
        headers={"X-Workspace-ID": "workspace-a"},
    )
    assert status_response.status_code == 200
    assert "snapshot" not in status_response.json()


def test_finalize_api_rejects_stale_revision_before_cos(api_fixture) -> None:
    client, task_id, request_body = api_fixture
    request_body["items"][0]["expected_preview_revision"] -= 1
    response = client.post(
        f"/product-processing/tasks/{task_id}/preview/finalize",
        headers={"X-Workspace-ID": "workspace-a"}, json=request_body,
    )
    assert response.status_code == 409
    assert "revision" in response.json()["detail"]


def test_finalize_api_is_workspace_scoped(api_fixture) -> None:
    client, task_id, request_body = api_fixture
    response = client.post(
        f"/product-processing/tasks/{task_id}/preview/finalize",
        headers={"X-Workspace-ID": "workspace-b"}, json=request_body,
    )
    assert response.status_code in {404, 409}
```

- [ ] **Step 2: Run API tests and confirm routes are absent**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_finalize.py -q
```

Expected: finalize route returns 404.

- [ ] **Step 3: Define typed save/finalize inputs**

Use these schemas:

```python
class PreviewImageManifestInput(BaseModel):
    main_asset_id: str = ""
    carousel_asset_ids: list[str] = Field(default_factory=list)
    detail_asset_ids: list[str] = Field(default_factory=list)


class PreviewDesiredState(BaseModel):
    title: str
    description: str
    core_fields: dict[str, Any] = Field(default_factory=dict)
    image_manifest_v2: PreviewImageManifestInput


class PreviewSaveItem(BaseModel):
    product_draft_id: int
    expected_preview_revision: int = Field(ge=0)
    overrides: PreviewDesiredState


class PreviewSaveRequest(BaseModel):
    items: list[PreviewSaveItem] = Field(default_factory=list)


class PreviewFinalizeRequest(PreviewSaveRequest):
    pass
```

Pass `item.model_dump()` to the service so `image_manifest_v2` is normalized before persistence. Text and core fields stay in the same desired-state item so the finalize snapshot captures unsaved edits atomically.

- [ ] **Step 4: Add finalize, status, and retry routes**

Add matching wrappers to `ProductProcessingService`:

```python
def begin_preview_finalize(self, task_id: int, items: list[dict[str, Any]], *,
                           workspace_id: str = "local") -> dict[str, Any]:
    self._require_task(task_id, workspace_id)
    if not bool(self.engine_status()["diagnostics"]["config"].get("cos_configured")):
        raise ProductProcessingConflict("COS 图床未配置，请先在系统设置完成配置")
    try:
        return self.preview_images.begin_finalize(task_id, items, workspace_id=workspace_id)
    except PreviewRevisionConflict as exc:
        raise ProductProcessingConflict(str(exc)) from exc

def preview_finalize_status(self, task_id: int, run_id: str, *,
                            workspace_id: str = "local") -> dict[str, Any]:
    self._require_task(task_id, workspace_id)
    run = self.preview_images.get_finalize(run_id, workspace_id=workspace_id)
    if int(run["task_id"]) != int(task_id):
        raise ProductProcessingNotFound("preview finalization run not found")
    return run

def retry_preview_finalize(self, task_id: int, run_id: str, *,
                           workspace_id: str = "local") -> dict[str, Any]:
    self._require_task(task_id, workspace_id)
    current = self.preview_images.get_finalize(run_id, workspace_id=workspace_id)
    if int(current["task_id"]) != int(task_id):
        raise ProductProcessingNotFound("preview finalization run not found")
    return self.preview_images.retry_finalize(run_id, workspace_id=workspace_id)

def preview_finalize_download_path(self, task_id: int, run_id: str, *,
                                   workspace_id: str = "local") -> Path:
    self._require_task(task_id, workspace_id)
    return self.preview_images.finalize_download_path(
        run_id, task_id, workspace_id=workspace_id
    )
```

Add:

```python
@router.post("/tasks/{task_id}/preview/finalize", status_code=status.HTTP_202_ACCEPTED)
def finalize_preview(task_id: int, body: PreviewFinalizeRequest,
                     workspace_id: str = Header(default="local", alias="X-Workspace-ID")) -> dict[str, Any]:
    return _call(service.begin_preview_finalize, task_id,
                 [item.model_dump() for item in body.items],
                 workspace_id=_workspace(workspace_id))


@router.get("/tasks/{task_id}/preview/finalize/{run_id}")
def preview_finalize_status(task_id: int, run_id: str,
                            workspace_id: str = Header(default="local", alias="X-Workspace-ID")) -> dict[str, Any]:
    return _call(service.preview_finalize_status, task_id, run_id,
                 workspace_id=_workspace(workspace_id))


@router.post("/tasks/{task_id}/preview/finalize/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_preview_finalize(task_id: int, run_id: str,
                           workspace_id: str = Header(default="local", alias="X-Workspace-ID")) -> dict[str, Any]:
    return _call(service.retry_preview_finalize, task_id, run_id,
                 workspace_id=_workspace(workspace_id))


@router.get("/tasks/{task_id}/preview/finalize/{run_id}/download")
def download_preview_finalize(task_id: int, run_id: str,
                              workspace_id: str = Header(default="local", alias="X-Workspace-ID")):
    path = _call(service.preview_finalize_download_path, task_id, run_id,
                 workspace_id=_workspace(workspace_id))
    return FileResponse(path, filename=path.name)
```

Public run DTO contains `id`, `task_id`, `status`, `total_count`, `published_count`, `failed_count`, sanitized `errors`, `workbook_ready`, `file`, `row_count`, `product_count`, and `download`. It omits snapshot overrides, local paths and claim state.

`preview_finalize_download_path()` must verify workspace, task ID, run ID and `status == "completed"`, then resolve the persisted path through `assets.require_managed_file`. Make the old `/preview/export` endpoint start a run from currently saved revisions and return HTTP 202-compatible run data; it must not synchronously write a workbook. The old `download?kind=dxm_final` may serve only the most recent completed run for compatibility, while the new UI always uses the run-specific download URL.

- [ ] **Step 5: Run API tests and commit routes**

Run the Task 9 pytest command. Expected: all finalize API tests pass.

```powershell
git add local-runtime/wh_local/modules/product_processing/api/schemas.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_preview_finalize.py
git commit -m "feat(product-processing): expose preview finalization API"
```

## Task 10: Add typed frontend manifest operations and API functions

**Files:**
- Create: `web-frontend/src/modules/product_processing/data/precheckImageModel.ts`
- Create: `web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts`
- Modify: `web-frontend/src/modules/product_processing/types/index.ts:168-220`
- Modify: `web-frontend/src/modules/product_processing/api/productProcessingApi.ts`

- [ ] **Step 1: Write failing pure-model tests**

Create `precheckImageModel.test.ts`:

```typescript
import assert from "node:assert/strict";
import test from "node:test";
import {
  addAssets,
  moveAsset,
  removeAsset,
  restoreRemovedAsset,
  selectMainAsset,
} from "./precheckImageModel.ts";

const base = {
  main_asset_id: "a",
  carousel_asset_ids: ["a", "b", "c"],
  detail_asset_ids: ["d"],
};

test("carousel add appends and never replaces main", () => {
  assert.deepEqual(addAssets(base, "carousel", ["x", "y"]), {
    ...base,
    carousel_asset_ids: ["a", "b", "c", "x", "y"],
  });
});

test("removing main selects the next carousel asset", () => {
  const result = removeAsset(base, "carousel", "a");
  assert.equal(result.manifest.main_asset_id, "b");
  assert.deepEqual(result.manifest.carousel_asset_ids, ["b", "c"]);
  assert.deepEqual(restoreRemovedAsset(result.manifest, result.undo), base);
});

test("removing all details preserves an explicit empty list", () => {
  const result = removeAsset(base, "detail", "d");
  assert.deepEqual(result.manifest.detail_asset_ids, []);
});

test("reorder and select-main are identity based", () => {
  assert.deepEqual(moveAsset(base, "carousel", "c", -1).carousel_asset_ids, ["a", "c", "b"]);
  assert.equal(selectMainAsset(base, "c").main_asset_id, "c");
});
```

- [ ] **Step 2: Run Node tests and confirm the model is missing**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts
```

Expected: module-not-found failure.

- [ ] **Step 3: Define frontend types and pure immutable operations**

Add these types to `types/index.ts`:

```typescript
export type PreviewImageOrigin = "source" | "generated" | "dimension" | "upload";
export type PreviewImageAsset = {
  id: string;
  origin: PreviewImageOrigin;
  preview_url: string;
  publication_status: "local" | "materializing" | "ready" | "publishing" | "published" | "publish_failed";
  public_url: string | null;
  width: number;
  height: number;
};
export type PreviewImageManifest = {
  main_asset_id: string;
  carousel_asset_ids: string[];
  detail_asset_ids: string[];
};
export type PreviewFinalizeRun = {
  id: string;
  task_id: number;
  status: "queued" | "publishing" | "publish_failed" | "stale" | "completed";
  total_count: number;
  published_count: number;
  failed_count: number;
  errors: Array<{ asset_id: string; product_draft_id: number; code: string; message: string }>;
  workbook_ready: boolean;
  file: string;
  row_count: number;
  product_count: number;
  download: string;
};
```

Change `PreviewItem` to include `preview_revision`, `exportable`, `assets`, and `image_manifest`. Implement model functions without mutating input arrays; `RemovedAssetUndo` records target, asset ID, original index and previous main ID. Deduplicate only newly added IDs while preserving order.

- [ ] **Step 4: Add typed API functions**

In `productProcessingApi.ts`, use the existing product-processing request client conventions to expose:

```typescript
export type PreviewSavePayload = {
  product_draft_id: number;
  expected_preview_revision: number;
  overrides: {
    title: string;
    description: string;
    core_fields: PreviewCoreFields;
    image_manifest_v2: PreviewImageManifest;
  };
};
export type PreviewSaveResponse = {
  saved_count: number;
  items: Array<{ product_draft_id: number; preview_revision: number }>;
};

export async function uploadPreviewAssets(
  ctx: ApiContext, taskId: number, draftId: number, files: File[],
): Promise<{ assets: PreviewImageAsset[] }> {
  const form = new FormData();
  form.append("draft_id", String(draftId));
  files.forEach((file) => form.append("image_files", file));
  return ppUpload(ctx, `/api/product-processing/tasks/${taskId}/preview/assets`, form);
}

export function saveProductPreview(
  ctx: ApiContext, taskId: number, items: PreviewSavePayload[],
): Promise<PreviewSaveResponse> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview`, {
    method: "PATCH", body: { items },
  });
}

export function finalizeProductPreview(
  ctx: ApiContext, taskId: number, items: PreviewSavePayload[],
): Promise<PreviewFinalizeRun> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/finalize`, {
    method: "POST", body: { items },
  });
}

export function getPreviewFinalizeRun(
  ctx: ApiContext, taskId: number, runId: string,
): Promise<PreviewFinalizeRun> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/finalize/${encodeURIComponent(runId)}`);
}

export function retryPreviewFinalizeRun(
  ctx: ApiContext, taskId: number, runId: string,
): Promise<PreviewFinalizeRun> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/finalize/${encodeURIComponent(runId)}/retry`, {
    method: "POST", body: {},
  });
}
```

Import `ppRequest`, `ppUpload`, and `ApiContext` from `./client`, and import the referenced preview types from `../types`. Define `PreviewSaveResponse` with `saved_count` and returned item revisions. `PreviewSavePayload` always includes the complete current title, description, core fields, manifest, product draft ID and expected revision.

- [ ] **Step 5: Run model tests/build and commit frontend contracts**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: model tests pass and TypeScript/Vite build succeeds.

```powershell
git add web-frontend/src/modules/product_processing/data/precheckImageModel.ts web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts web-frontend/src/modules/product_processing/types/index.ts web-frontend/src/modules/product_processing/api/productProcessingApi.ts
git commit -m "feat(product-processing): model preview image edits by asset id"
```

## Task 11: Build smooth add/delete/reorder controls for every output image section

**Files:**
- Create: `web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx:30-430`
- Modify: `web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css:1028-1180`
- Modify: `web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts`

- [ ] **Step 1: Add model tests for multi-upload and undo expiry data**

Append:

```typescript
test("new main is inserted into carousel exactly once", () => {
  const added = addAssets(base, "main", ["x"]);
  assert.equal(added.main_asset_id, "x");
  assert.deepEqual(added.carousel_asset_ids, ["a", "b", "c", "x"]);
  assert.deepEqual(addAssets(added, "main", ["x"]).carousel_asset_ids, added.carousel_asset_ids);
});

test("deleting the final carousel image leaves a visible no-main state", () => {
  const result = removeAsset(
    { main_asset_id: "a", carousel_asset_ids: ["a"], detail_asset_ids: [] },
    "carousel",
    "a",
  );
  assert.equal(result.manifest.main_asset_id, "");
  assert.deepEqual(result.manifest.carousel_asset_ids, []);
});
```

- [ ] **Step 2: Run the model test before UI edits**

Run the Task 10 Node command. Expected: the new main-insertion contract fails until the model handles target `main`.

- [ ] **Step 3: Implement the image manager component**

`PrecheckImageManager` receives:

```typescript
type PrecheckImageManagerProps = {
  assets: PreviewImageAsset[];
  manifest: PreviewImageManifest;
  disabled: boolean;
  onAddFiles: (target: "main" | "carousel" | "detail", files: File[]) => void;
  onManifestChange: (manifest: PreviewImageManifest) => void;
  onPreview: (url: string) => void;
  onUndoAvailable: (undo: RemovedAssetUndo) => void;
};
```

Render four groups:

- Available asset library: show source, generated, dimension and uploaded assets with origin badges plus `设为主图`, `加入轮播`, `加入详情` actions. Removing an output reference leaves the asset here so it can be added again.
- Main: selected asset, origin badge, `添加或更换主图`, and delete.
- Carousel: ordered cards with `设主图`, move-left, move-right and delete.
- Detail: ordered cards with move-left, move-right and delete.

Every file input accepts `image/jpeg,image/png,image/webp`, uses `multiple`, copies `Array.from(event.currentTarget.files ?? [])`, and resets its value. Use real `<button type="button">` controls; do not make the image itself a destructive action. Delete immediately updates the manifest and sends the undo record upward.

- [ ] **Step 4: Convert the precheck page from URL edits to asset-ID edits**

Replace `main_image`, `carousel_images`, and `detail_images` in `ItemEdits` with:

```typescript
type ItemEdits = {
  title?: string;
  description?: string;
  imageManifest?: PreviewImageManifest;
  addedAssets?: PreviewImageAsset[];
  core_fields?: PreviewCoreFields;
};
```

On upload, merge returned assets by ID, then call `addAssets()` for the chosen target. Replace `collectOverrides()` with `collectDesiredState()`, which always returns the effective title, description, complete core fields and effective `image_manifest_v2`, including empty arrays. Separately compare this value with the item’s initially loaded desired state to calculate `dirtyCount`. Save payload includes the item revision. After a successful save, reload from the returned revisions.

Maintain one undo snackbar `{draftId, undo, expiresAt}` for five seconds; restoring modifies only the matching draft's current manifest. Disable all mutation controls while that task is finalizing.

- [ ] **Step 5: Style, build, and commit image controls**

Add keyboard-visible focus, origin badges, compact action overlays, empty-state add buttons, a non-blocking undo snackbar, and responsive wrapping. Use existing verify-page colors and button classes; do not introduce a separate theme.

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: tests and build pass.

```powershell
git add web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css web-frontend/src/modules/product_processing/data/precheckImageModel.ts web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts
git commit -m "feat(product-processing): add and remove precheck images"
```

## Task 12: Add resilient finalize progress, failure retry, and automatic download

**Files:**
- Create: `web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.ts`
- Create: `web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts`
- Create: `web-frontend/src/modules/product_processing/components/PrecheckFinalizeProgress.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css`

- [ ] **Step 1: Write failing polling race tests**

Create tests covering one in-flight request, stale response suppression and cleanup:

```typescript
import assert from "node:assert/strict";
import test from "node:test";
import { createPrecheckFinalizeRefresh } from "./precheckFinalizeRefresh.ts";

test("an older response cannot replace a newer finalize run", async () => {
  const resolvers: Array<(value: { id: string; status: string }) => void> = [];
  const seen: string[] = [];
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: () => new Promise((resolve) => resolvers.push(resolve)),
    onRun: (run) => seen.push(run.id),
    onError: () => undefined,
    intervalMs: 20,
  });
  refresh.watch("old");
  refresh.watch("new");
  resolvers[0]({ id: "old", status: "completed" });
  await Promise.resolve();
  assert.deepEqual(seen, []);
  refresh.stop();
});

test("stop clears timers and prevents later writes", async () => {
  let writes = 0;
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: async () => ({ id: "run", status: "publishing" }),
    onRun: () => { writes += 1; },
    onError: () => undefined,
    intervalMs: 10,
  });
  refresh.watch("run");
  refresh.stop();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(writes, 0);
});
```

- [ ] **Step 2: Run polling tests and confirm missing controller**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement a one-in-flight latest-wins controller**

The controller exposes `watch(runId)`, `setVisible(visible)`, and `stop()`. Keep a monotonically increasing generation; capture it before every request and ignore success or failure when it no longer matches. Do not issue a second request while one is in flight. Pause timers while hidden and immediately refresh on becoming visible. Stop polling for `completed`, `publish_failed`, or `stale`.

- [ ] **Step 4: Build progress UI and replace direct export**

`PrecheckFinalizeProgress` renders:

- progress bar and `已发布 n / total` while queued/publishing;
- failed asset thumbnails and sanitized messages for `publish_failed`;
- `仅重试失败图片` button;
- revision-changed guidance for `stale`;
- completed product/row counts and download button.

In `ProductProcessingPrecheckPage`:

1. Rename the action to `完成预审并导出`.
2. Submit every `exportable` item's effective edits and revision with `finalizeProductPreview`; do not call `saveAll` first and do not allow the browser to omit an exportable task item.
3. Store `run.id`, start the refresh controller, and persist the ID in `sessionStorage` under `pp-preview-finalize:<workspace>:<taskId>`.
4. Restore and query that run on page reload.
5. Lock current task edits while queued/publishing.
6. On completion, clear the session key and call `ppDownload(ctx, run.download, run.file)` exactly once; never use a fixed task-level final workbook path.
7. On failure, retain every local edit and expose only-failed retry.
8. On stale, reload server data only after warning that unsaved local edits remain available.

- [ ] **Step 5: Run frontend tests/build and commit finalization UI**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: all Node tests pass and build succeeds.

```powershell
git add web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.ts web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts web-frontend/src/modules/product_processing/components/PrecheckFinalizeProgress.tsx web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css
git commit -m "feat(product-processing): show resumable preview finalization"
```

## Task 13: Run compatibility, security, and browser acceptance gates

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/README.md`
- Modify: `docs/superpowers/specs/2026-08-13-product-precheck-deferred-cos-image-manifest-design.md` only if implementation reveals a confirmed contract correction
- Test: all product-processing backend and frontend suites

- [ ] **Step 1: Add final regression assertions before the full run**

Ensure tests explicitly assert all of these values are rejected from final workbook image fields:

```python
@pytest.mark.parametrize("value", [
    "/pp-media/task/image.jpg",
    r"C:\\Users\\user\\image.jpg",
    "file:///tmp/image.jpg",
    "http://127.0.0.1/image.jpg",
    "http://192.168.1.20/image.jpg",
    "https://localhost/image.jpg",
])
def test_final_workbook_rejects_non_public_image_values(value: str) -> None:
    with pytest.raises(ValueError, match="public HTTPS"):
        require_final_public_image_urls([value])
```

Add a cross-workspace API test proving workspace B cannot list, reference, finalize, retry, or download workspace A's asset/run.

- [ ] **Step 2: Run the focused backend gate**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_preview_finalize.py local-runtime/tests/test_product_processing_preview_overrides.py local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_image_quality.py local-runtime/tests/test_product_processing_image_slots.py -q
```

Expected: all selected tests pass with no real AI or COS calls.

- [ ] **Step 3: Run the complete backend and frontend gates**

Run:

```powershell
& 'C:\Python314\python.exe' -X utf8 -m pytest local-runtime/tests -q
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts web-frontend/src/modules/product_processing/data/precheckFinalizeRefresh.test.ts web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts web-frontend/src/modules/product_processing/data/dimensionCanvasAutosaveModel.test.ts web-frontend/src/modules/product_processing/data/dimensionNotificationRefresh.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: complete backend suite, all selected Node tests, TypeScript check and Vite build pass.

- [ ] **Step 4: Update module documentation**

Document these exact lifecycle states in the README:

```text
AI/用户/尺寸图片 → 工作区本地受管素材 → 预审按 asset_id 增删排序
→ 完成预审快照 → 内容哈希 COS 发布 → HTTPS URL 解析 → 店小秘最终版表格
```

State that COS failure never falls back to `/pp-media`, generated assets are no longer uploaded during processing, and retry publishes only unresolved content hashes.

- [ ] **Step 5: Perform local browser acceptance without real COS writes**

Use a test configuration with a fake publisher or a local test double; do not use production credentials. Verify:

1. Open a completed task's precheck page.
2. Add two carousel files at once and confirm neither replaces the main image.
3. Accept a dimension image and confirm it appears with a “尺寸图” badge.
4. Delete generated and dimension images, undo one deletion, reorder remaining images, save and reload.
5. Delete all details and confirm the empty state persists.
6. Start finalization and observe progress without a frozen page.
7. Inject one publisher failure, verify no workbook download, then retry only the failed image.
8. Complete and inspect the workbook: every exported image value is public HTTPS and no local path is present.

- [ ] **Step 6: Commit documentation and final test adjustments**

```powershell
git add local-runtime/wh_local/modules/product_processing/README.md local-runtime/tests web-frontend/src/modules/product_processing/data
git commit -m "test(product-processing): verify deferred image publication"
```

After the commit, run `git status --short`. Expected: empty output. Do not package, deploy, restart services, or call real COS in this plan; those require a separate explicitly authorized release task.
