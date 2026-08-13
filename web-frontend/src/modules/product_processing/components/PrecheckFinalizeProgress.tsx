import type { PreviewFinalizeRun, PreviewImageAsset } from "../types";

type PrecheckFinalizeProgressProps = {
  run: PreviewFinalizeRun;
  assets: PreviewImageAsset[];
  retrying: boolean;
  downloading: boolean;
  onRetry: () => void;
  onDownload: () => void;
  onReloadStale: () => void;
};

function sanitizedMessage(message: string, code: string): string {
  const cleaned = message
    .replace(/\b[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*/g, "[本地路径已隐藏]")
    .replace(/\b\/(?:Users|home|tmp|var|opt)\/[^\s]*/g, "[本地路径已隐藏]")
    .replace(/\b(?:secret|token|api[_-]?key|password)\b\s*[:=]\s*\S+/gi, "[敏感信息已隐藏]")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .trim()
    .slice(0, 240);
  return cleaned || `图片发布失败（${code || "publish_failed"}）`;
}

export function PrecheckFinalizeProgress({
  run,
  assets,
  retrying,
  downloading,
  onRetry,
  onDownload,
  onReloadStale,
}: PrecheckFinalizeProgressProps) {
  const assetById = new Map(assets.map((asset) => [asset.id, asset]));
  const total = Math.max(0, run.total_count);
  const published = Math.min(Math.max(0, run.published_count), total || run.published_count);
  const progress = total > 0 ? Math.min(100, Math.round((published / total) * 100)) : 0;
  const active = run.status === "queued" || run.status === "publishing";

  return (
    <section className={`precheck-finalize status-${run.status}`} aria-live="polite">
      <header>
        <div>
          <span className="precheck-finalize-kicker">完成任务 · {run.id}</span>
          <h2>{active ? "正在发布最终保留图片" : run.status === "completed" ? "最终版已生成" : "完成任务需要处理"}</h2>
        </div>
        <span className={`precheck-finalize-status status-${run.status}`}>
          {({
            queued: "等待发布",
            publishing: "发布中",
            publish_failed: "发布失败",
            stale: "版本已变化",
            completed: "已完成",
          } as const)[run.status]}
        </span>
      </header>

      {active && (
        <div className="precheck-finalize-progress">
          <div className="precheck-progress-copy">
            <strong>已发布 {published} / {total}</strong>
            <span>{run.failed_count > 0 ? `失败 ${run.failed_count}` : run.status === "queued" ? "正在建立发布队列" : "正在生成可信 HTTPS 图片地址"}</span>
          </div>
          <progress max={Math.max(1, total)} value={published} aria-label={`已发布 ${published} / ${total}`} />
          <span className="precheck-progress-percent">{progress}%</span>
        </div>
      )}

      {run.status === "publish_failed" && (
        <div className="precheck-finalize-failures">
          <p>必需图片未全部发布，最终表格尚未生成。已成功素材不会重复上传。</p>
          {run.errors.length > 0 ? (
            <ul>
              {run.errors.map((failure, index) => {
                const asset = assetById.get(failure.asset_id);
                const previewUrl = asset?.preview_url || asset?.public_url || "";
                return (
                  <li key={`${failure.asset_id}-${failure.product_draft_id}-${index}`}>
                    {previewUrl ? <img src={previewUrl} alt="发布失败图片" /> : <span className="precheck-failure-placeholder">无预览</span>}
                    <div>
                      <strong>商品 #{failure.product_draft_id}</strong>
                      <span>{sanitizedMessage(failure.message, failure.code)}</span>
                      <small>{failure.code || "publish_failed"} · {failure.asset_id}</small>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="precheck-manager-empty">发布任务失败，暂无单图错误明细。</div>
          )}
          <button type="button" className="primary" disabled={retrying} onClick={onRetry}>
            {retrying ? "正在重试…" : "仅重试失败图片"}
          </button>
        </div>
      )}

      {run.status === "stale" && (
        <div className="precheck-finalize-stale">
          <strong>预检版本已变化，旧快照不会覆盖当前商品。</strong>
          <p>页面中的未保存编辑仍保留。重新读取最新服务端版本后，可核对并再次完成预审。</p>
          <button type="button" onClick={onReloadStale}>保留本地编辑并刷新版本</button>
        </div>
      )}

      {run.status === "completed" && (
        <div className="precheck-finalize-completed">
          <div><strong>{run.product_count}</strong><span>商品</span></div>
          <div><strong>{run.row_count}</strong><span>表格行</span></div>
          <button
            type="button"
            className="primary"
            disabled={downloading || !run.workbook_ready || !run.download}
            onClick={onDownload}
          >
            {downloading ? "下载中…" : "下载最终版表格"}
          </button>
        </div>
      )}
    </section>
  );
}

export default PrecheckFinalizeProgress;
