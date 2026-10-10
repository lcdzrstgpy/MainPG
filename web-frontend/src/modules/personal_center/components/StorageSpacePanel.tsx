import { useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { sweepPodCache, type PodCacheSweepResult } from "../api/personalCenterApi";

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/**
 * 存储空间：一键清理本机图片缓存（POD 本地缓存 + 无引用孤儿文件）。
 *
 * 后端只清「没有任何记录引用的本地文件」：模板、在用资产与被其它记录共享的
 * 文件都有完整护栏（见 docs/POD-本地图片缓存与清理说明.md）。
 * 重复点击是幂等的，第二次通常不会有可释放的空间。
 */
export function StorageSpacePanel() {
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<PodCacheSweepResult | null>(null);
  const busyRef = useRef(false);

  const sweep = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const result = await sweepPodCache();
      setLastResult(result);
      if (result.files_removed > 0) {
        showToast(`已释放 ${formatBytes(result.bytes_freed)}，共清理 ${result.files_removed} 个缓存文件`, "success");
      } else {
        showToast("没有可释放的空间，本地缓存已经很干净了", "info");
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "清理失败，请稍后重试", "error");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <article className="personal-card storage-card">
      <div className="personal-card-title">
        <span className="iconfont icon-database" aria-hidden="true" />
        <div>
          <h2>存储空间</h2>
          <small>清理本机图片缓存释放磁盘空间；使用中的图片与模板不会被删除。</small>
        </div>
        <button type="button" onClick={() => void sweep()} disabled={busy}>
          {busy ? "清理中…" : "一键清理"}
        </button>
      </div>
      {lastResult && (
        <p className="storage-result" role="status">
          上次清理：释放 {formatBytes(lastResult.bytes_freed)}（{lastResult.files_removed} 个文件）
        </p>
      )}
    </article>
  );
}
