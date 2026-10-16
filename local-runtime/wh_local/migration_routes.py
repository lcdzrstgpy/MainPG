"""数据迁移 HTTP 路由：个人中心的「数据迁移」卡片就靠这几个接口。

安全约定：
  - 除了「下载迁移包」，其余接口都要求本机登录会话（与个人中心其它接口一致）。
  - 下载接口**不校验会话**：浏览器要原生下载（大文件走磁盘流，不能塞进前端内存），
    而原生下载带不了 Authorization 头。job_id 是 128 位随机串、只在鉴权后的
    状态轮询里返回，本身就是不可猜的能力凭据；服务又只监听回环地址，
    因此这里以 job_id 作为授权依据。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import migration


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "state": job["state"],
        "phase": job["phase"],
        "done": job["done"],
        "total": job["total"],
        "message": job["message"],
        "result": job["result"],
    }


def create_migration_router(
    data_dir: Path,
    db_path: Path | None,
    sessions: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/migration", tags=["migration"])
    jobs = migration.MigrationJobs(data_dir, db_path)

    def require_session(authorization: str | None = Header(default=None)) -> Any:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[len("bearer ") :].strip()
        if not token:
            raise HTTPException(status_code=401, detail="missing bearer token")
        session = sessions.store.get_session(token)
        if session is None:
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return session

    @router.get("/plan")
    def migration_plan(_session: Any = Depends(require_session)) -> dict[str, Any]:
        """迁移包内容清单 + 是否有待重启生效的导入。"""
        plan = migration.collect_plan(data_dir, db_path)
        plan["pending"] = migration.pending_info(data_dir)
        return plan

    @router.post("/export")
    def start_export(_session: Any = Depends(require_session)) -> dict[str, Any]:
        job = jobs.start_export()
        return _public_job(job)

    @router.get("/export/{job_id}")
    def export_status(job_id: str, _session: Any = Depends(require_session)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None or job["kind"] != "export":
            raise HTTPException(status_code=404, detail="迁移导出任务不存在或已过期")
        return _public_job(job)

    @router.get("/export/{job_id}/download")
    def download_export(job_id: str) -> FileResponse:
        job = jobs.get(job_id)
        if job is None or job["kind"] != "export":
            raise HTTPException(status_code=404, detail="迁移导出任务不存在或已过期")
        if job["state"] != "done":
            raise HTTPException(status_code=409, detail="迁移包还没打包完")
        path = Path(str((job.get("result") or {}).get("path") or ""))
        if not path.is_file():
            raise HTTPException(status_code=404, detail="迁移包已不存在，请重新导出")
        return FileResponse(
            path, media_type="application/zip", filename=path.name
        )

    @router.post("/import")
    async def start_import(
        archive: UploadFile = File(...),
        _session: Any = Depends(require_session),
    ) -> dict[str, Any]:
        if migration.pending_info(data_dir) is not None:
            raise HTTPException(
                status_code=409,
                detail="上一次导入还没重启生效，请先重启工作台再导入新的迁移包",
            )
        upload_dir = data_dir / migration.UPLOAD_DIR_NAME
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{uuid.uuid4().hex}{migration.ARCHIVE_SUFFIX}"
        try:
            with open(target, "wb") as dst:
                while True:
                    chunk = await archive.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"迁移包保存失败：{exc}") from exc
        return _public_job(jobs.start_import(target))

    @router.get("/import/{job_id}")
    def import_status(job_id: str, _session: Any = Depends(require_session)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None or job["kind"] != "import":
            raise HTTPException(status_code=404, detail="迁移导入任务不存在或已过期")
        return _public_job(job)

    return router
