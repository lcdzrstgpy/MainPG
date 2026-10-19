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

#: 迁移包体积上限。挡的是「误选一个几十 GB 的镜像文件」把数据盘写满，
#: 不是挡正常用户 —— 素材多的机器几 GB 很常见，给足余量。
MAX_UPLOAD_BYTES = 64 * 1024 * 1024 * 1024


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
    *,
    service: Any = None,
) -> APIRouter:
    """``service`` 是内嵌的 ClipForge 服务：导入会覆盖它正在用的库与素材，
    因此导入前要停、导入后要恢复（见 MigrationJobs._pause_embedded_service）。"""
    router = APIRouter(prefix="/api/migration", tags=["migration"])
    jobs = migration.MigrationJobs(data_dir, db_path, service)

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
        try:
            job = jobs.start_export()
        except RuntimeError as exc:
            # 已有任务在跑：明确 409，别让用户看到 500 或重复点出两个导出。
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        # 上一次上传如果中途失败/进程被杀，这里可能留着几个 GB 的死文件。
        for stale in upload_dir.glob(f"*{migration.ARCHIVE_SUFFIX}"):
            stale.unlink(missing_ok=True)
        target = upload_dir / f"{uuid.uuid4().hex}{migration.ARCHIVE_SUFFIX}"
        written = 0
        try:
            with open(target, "wb") as dst:
                while True:
                    chunk = await archive.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "迁移包超过 64 GB 上限，请确认选的是 .mainpgbak 备份包"
                                "（不是素材盘里的其它大文件）"
                            ),
                        )
                    dst.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"迁移包保存失败：{exc}") from exc
        try:
            job = jobs.start_import(target)
        except RuntimeError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _public_job(job)

    @router.get("/import/{job_id}")
    def import_status(job_id: str, _session: Any = Depends(require_session)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None or job["kind"] != "import":
            raise HTTPException(status_code=404, detail="迁移导入任务不存在或已过期")
        return _public_job(job)

    return router
