import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import {
  type MigrationJob,
  type MigrationPlan,
  fetchMigrationJob,
  fetchMigrationPlan,
  migrationDownloadUrl,
  startMigrationExport,
  uploadMigrationArchive,
} from "../api/migrationApi";

const POLL_INTERVAL_MS = 1000;
/** 轮询上限：十几 GB 的包在慢盘上要跑很久，给足 2 小时。 */
const POLL_LIMIT = 7200;
const MAX_CONSECUTIVE_POLL_FAILURES = 5;

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index >= 2 ? 1 : 0)} ${units[index]}`;
}

function jobPercent(job: MigrationJob | null): number {
  if (!job || job.total <= 0) return 0;
  return Math.min(100, Math.round((job.done / job.total) * 100));
}

/**
 * 数据迁移：换新电脑时把旧机的全部业务数据打包带走，在新机一键还原。
 *
 * 两个必须让用户看懂的点：
 *  - 迁移包带的是**整库快照**（主键、外键、workspace 归属全保留），不是逐表合并——
 *    新机本来就是空的，合并反而会让数据「看得见却找不到」；
 *  - 登录态与密钥**不随包走**（Windows DPAPI 换机必然解不开），新机重新登录即可。
 *
 * 导入的数据库要等下次启动、开库之前才替换（运行期热替换 SQLite 文件会损坏数据），
 * 素材文件则是当场就地还原。所以导入完成后必须提示用户重启工作台。
 */
export function DataMigrationPanel() {
  const [plan, setPlan] = useState<MigrationPlan | null>(null);
  const [planError, setPlanError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [exportJob, setExportJob] = useState<MigrationJob | null>(null);
  const [importJob, setImportJob] = useState<MigrationJob | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const busyRef = useRef(false);
  const cancelledRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refreshPlan = useCallback(async () => {
    setPlanError("");
    setLoading(true);
    try {
      setPlan(await fetchMigrationPlan());
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "读取迁移清单失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void refreshPlan();
    return () => {
      cancelledRef.current = true;
    };
  }, [refreshPlan]);

  /** 轮询后台任务：单次查询失败不放弃（网络/重启抖动很常见），连续失败才认输。 */
  const pollJob = useCallback(
    async (
      kind: "export" | "import",
      jobId: string,
      onTick: (job: MigrationJob) => void,
    ): Promise<MigrationJob | null> => {
      let failures = 0;
      for (let step = 0; step < POLL_LIMIT; step += 1) {
        if (cancelledRef.current) return null;
        try {
          const job = await fetchMigrationJob(kind, jobId);
          failures = 0;
          onTick(job);
          if (job.state !== "running") return job;
        } catch (error) {
          failures += 1;
          if (failures >= MAX_CONSECUTIVE_POLL_FAILURES) throw error;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
      }
      return null;
    },
    [],
  );

  const runExport = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setExportJob(null);
    try {
      const started = await startMigrationExport();
      setExportJob(started);
      const finished = await pollJob("export", started.job_id, setExportJob);
      if (!finished) {
        showToast("导出还在跑，请稍后回本页查看进度", "info");
        return;
      }
      if (finished.state !== "done") {
        showToast(finished.message || "导出失败", "error");
        return;
      }
      // 迁移包可能有几 GB，走浏览器原生下载（磁盘流），不要用 fetch 读进内存。
      const link = document.createElement("a");
      link.href = migrationDownloadUrl(started.job_id);
      link.download = "";
      document.body.appendChild(link);
      link.click();
      link.remove();
      showToast(
        `迁移包已开始下载（${formatBytes(finished.result?.bytes ?? 0)}，含全部业务数据，请妥善保管、不要外发）`,
        "success",
      );
      const skipped = finished.result?.skipped_assets ?? 0;
      if (skipped > 0) {
        showToast(
          `有 ${skipped} 个素材文件读取失败、没进包（可能被杀毒软件占用），到新机可能缺这些图`,
          "error",
        );
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "导出失败", "error");
    } finally {
      busyRef.current = false;
      setBusy(false);
      void refreshPlan();
    }
  };

  const runImport = async (file: File) => {
    setPendingFile(null);
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setImportJob(null);
    try {
      const started = await uploadMigrationArchive(file);
      setImportJob(started);
      const finished = await pollJob("import", started.job_id, setImportJob);
      if (!finished) {
        showToast("导入还在跑，请稍后重启工作台确认结果", "info");
        return;
      }
      if (finished.state !== "done") {
        showToast(finished.message || "导入失败", "error");
        return;
      }
      const failed = finished.result?.asset_errors ?? 0;
      const notice = finished.result?.version_notice ?? "";
      const saveRoot = finished.result?.save_root_notice ?? "";
      const overwritten = finished.result?.asset_overwritten ?? 0;
      const base =
        failed > 0
          ? `迁移包已导入（${failed} 个素材文件没还原成功），请重新打开工作台让它生效`
          : "迁移包已导入，请关闭并重新打开工作台让它生效";
      const extra = [
        overwritten > 0 ? `覆盖了 ${overwritten} 个同名素材文件` : "",
        notice,
        saveRoot ? `产品库目录已改到本机默认位置（原路径在本机不可用）：${saveRoot}` : "",
      ].filter(Boolean).join("；");
      showToast(extra ? `${base}（${extra}）` : base, failed > 0 ? "info" : "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "导入失败", "error");
    } finally {
      busyRef.current = false;
      setBusy(false);
      void refreshPlan();
    }
  };

  const hasPending = Boolean(plan?.pending);
  const runningJob = exportJob?.state === "running" ? exportJob : importJob?.state === "running" ? importJob : null;

  return (
    <article className="personal-card storage-card">
      <div className="personal-card-title">
        <span className="iconfont icon-database" aria-hidden="true" />
        <div>
          <h2>数据迁移</h2>
          <small>
            换新电脑时，把本机的草稿、来源图、POD 资产、采集与选品记录、店铺与工作台设置
            整体打包成一个文件；在新电脑上导入即可继续办公，不用从头再来。
          </small>
        </div>
        <button type="button" onClick={() => void refreshPlan()} disabled={busy || loading}>
          {loading ? "读取中…" : "重新读取"}
        </button>
      </div>

      {loading && !plan && (
        <p className="storage-hint">正在统计迁移包内容（素材多的机器需要几秒），请稍候…</p>
      )}

      {planError && (
        <p className="storage-hint storage-hint-error" role="alert">
          {planError}
        </p>
      )}

      {hasPending && (
        <p
          className={`storage-hint${plan?.pending?.apply_error ? " storage-hint-error" : ""}`}
          role={plan?.pending?.apply_error ? "alert" : "status"}
        >
          {plan?.pending?.apply_error ? (
            <>
              上次启动时没能替换数据库：<b>{plan.pending.apply_error}</b>
              。请先关闭占用工作台数据目录的程序（包括同时打开的另一个工作台），再重新打开工作台。
            </>
          ) : (
            <>
              上次导入的数据已就绪：<b>请关闭并重新打开工作台</b>，它会自动替换数据库并生效
            </>
          )}
          {plan?.pending?.staged_at ? `（导入时间 ${plan.pending.staged_at}）` : ""}
          。生效前不能再次导入。
        </p>
      )}

      {hasPending && plan?.pending?.version_notice && (
        <p className="storage-hint" role="status">
          {plan.pending.version_notice}
        </p>
      )}

      {plan && (
        <>
          <div className="storage-group">
            <p className="storage-group-title">迁移包内容</p>
            <ul className="storage-category-list">
              <li className="storage-category">
                <label>
                  <span className="storage-category-body">
                    <span className="storage-category-label">业务数据库</span>
                    <span className="storage-category-hint">
                      {plan.database.tables} 张表 · {plan.database.rows} 行记录
                    </span>
                  </span>
                  <span className="storage-category-size">{formatBytes(plan.database.bytes)}</span>
                </label>
              </li>
              {plan.assets.map((asset) => (
                <li key={asset.group} className="storage-category">
                  <label>
                    <span className="storage-category-body">
                      <span className="storage-category-label">{asset.group}</span>
                      <span className="storage-category-hint">{asset.path}</span>
                    </span>
                    <span className="storage-category-size">
                      {asset.files} 个 · {formatBytes(asset.bytes)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>

          <p className="storage-hint">
            合计 {plan.asset_files} 个素材文件 · 约 {formatBytes(plan.total_bytes)}。
            为了安全，迁移包<b>不包含</b>登录状态、账号密码、接口密钥与机器授权
            {plan.database.scrubbed.length > 0 ? `（共 ${plan.database.scrubbed.length} 项本机凭证）` : ""}
            ——新电脑导入后重新登录一次即可；积分与套餐以服务器为准，会自动同步。
          </p>
        </>
      )}

      {runningJob && (
        <p className="storage-hint" role="status">
          {runningJob.kind === "export" ? "正在打包" : "正在导入"}：{runningJob.phase}
          {runningJob.total > 0 ? `（${jobPercent(runningJob)}%）` : ""}，请不要关闭工作台。
        </p>
      )}

      {pendingFile && (
        <p className="storage-hint" role="alert">
          即将导入「{pendingFile.name}」（{formatBytes(pendingFile.size)}）。
          同名素材会被覆盖，数据库会在下次启动时整体替换（替换前自动备份当前数据）。
          <button type="button" onClick={() => void runImport(pendingFile)}>
            确认导入
          </button>
          <button type="button" onClick={() => setPendingFile(null)}>
            取消
          </button>
        </p>
      )}

      <div className="storage-actions">
        <button type="button" onClick={() => void runExport()} disabled={busy || loading || !plan}>
          {exportJob?.state === "running" ? "正在打包…" : "导出迁移包"}
        </button>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy || loading || hasPending}
        >
          {importJob?.state === "running" ? "正在导入…" : "导入迁移包"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".mainpgbak,.zip"
          style={{ display: "none" }}
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            // 清空 value：同一个文件连续选两次也要能触发 onChange。
            event.target.value = "";
            if (file) setPendingFile(file);
          }}
        />
      </div>
    </article>
  );
}
