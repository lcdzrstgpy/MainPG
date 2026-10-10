import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import {
  scanPodCache,
  sweepPodCache,
  type PodCacheScanCategory,
  type PodCacheSweepResult,
} from "../api/personalCenterApi";
import { computeScanSelection } from "./storageSelection";

/** 分类键 → 展示文案（后端返回未知键时回退为原键名）。 */
const CATEGORY_META: Record<string, { label: string; hint: string }> = {
  published_release: {
    label: "已发布图床的旧结果图",
    hint: "出图结果已在图床备份，释放本地副本",
  },
  terminal_intermediates: {
    label: "已结束批次的中间图",
    hint: "批次结束后不再使用的网格图、候选图案",
  },
  unreferenced_residue: {
    label: "历史无引用残留",
    hint: "已无任何记录引用的历史文件",
  },
  disk_orphans: {
    label: "磁盘孤儿文件",
    hint: "磁盘上没有任何记录认领的遗留文件",
  },
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/**
 * 存储空间：先扫描、再勾选清理本机图片缓存（POD 本地缓存 + 无引用孤儿文件）。
 *
 * 后端只清「没有任何记录引用的本地文件」：模板、在用资产与被其它记录共享的
 * 文件都有完整护栏（见 docs/POD-本地图片缓存与清理说明.md）。**未勾选的分类
 * 一步都不会执行**；重复清理是幂等的。
 */
export function StorageSpacePanel() {
  const [categories, setCategories] = useState<PodCacheScanCategory[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scanning, setScanning] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [scanError, setScanError] = useState("");
  const [lastResult, setLastResult] = useState<PodCacheSweepResult | null>(null);
  const busyRef = useRef(false);
  const seenScanRef = useRef(false);

  const scan = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setScanning(true);
    setScanError("");
    try {
      const data = await scanPodCache();
      setCategories(data.categories);
      // ⚠️ 必须在同步代码里先定「是否首次」：updater 延迟执行，内部读 ref 会永远是 false。
      const firstScan = !seenScanRef.current;
      seenScanRef.current = true;
      setSelected((previous) => computeScanSelection(data.categories, previous, firstScan));
    } catch (error) {
      setScanError(error instanceof Error ? error.message : "扫描失败，请稍后重试");
    } finally {
      busyRef.current = false;
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    void scan();
  }, [scan]);

  const toggle = (key: string, checked: boolean) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const cleanable = categories.filter((item) => item.files > 0);
  const selectedCleanable = cleanable.filter((item) => selected.has(item.key));
  const selectedBytes = selectedCleanable.reduce((total, item) => total + item.bytes, 0);
  const allSelected = cleanable.length > 0 && selectedCleanable.length === cleanable.length;
  const busy = scanning || sweeping;

  const sweep = async () => {
    const keys = selectedCleanable.map((item) => item.key);
    if (keys.length === 0) {
      showToast("请先勾选要清理的项目", "info");
      return;
    }
    if (busyRef.current) return;
    busyRef.current = true;
    setSweeping(true);
    try {
      const result = await sweepPodCache(keys);
      setLastResult(result);
      if (result.files_removed > 0) {
        showToast(
          `已释放 ${formatBytes(result.bytes_freed)}，共清理 ${result.files_removed} 个缓存文件`,
          "success",
        );
      } else {
        showToast("没有可释放的空间，勾选的项目已经很干净了", "info");
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "清理失败，请稍后重试", "error");
    } finally {
      busyRef.current = false;
      setSweeping(false);
      // 清理后重新扫描：数量与勾选状态一起刷新
      void scan();
    }
  };

  return (
    <article className="personal-card storage-card">
      <div className="personal-card-title">
        <span className="iconfont icon-database" aria-hidden="true" />
        <div>
          <h2>存储空间</h2>
          <small>勾选要清理的项目释放磁盘空间；使用中的图片与模板不会被删除。</small>
        </div>
        <button type="button" onClick={() => void scan()} disabled={busy}>
          {scanning ? "扫描中…" : "重新扫描"}
        </button>
      </div>

      {scanning && categories.length === 0 && (
        <p className="storage-hint">正在扫描可清理的项目…</p>
      )}
      {scanError && (
        <p className="storage-hint storage-hint-error" role="alert">
          {scanError}
        </p>
      )}

      {categories.length > 0 && (
        <ul className="storage-category-list">
          {categories.map((item) => {
            const meta = CATEGORY_META[item.key] ?? { label: item.key, hint: "" };
            const hasContent = item.files > 0;
            const checked = hasContent && selected.has(item.key);
            return (
              <li
                key={item.key}
                className={hasContent ? "storage-category" : "storage-category is-clean"}
              >
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!hasContent || busy}
                    onChange={(event) => toggle(item.key, event.target.checked)}
                  />
                  <span className="storage-category-body">
                    <span className="storage-category-label">{meta.label}</span>
                    {meta.hint && <span className="storage-category-hint">{meta.hint}</span>}
                  </span>
                  <span className="storage-category-size">
                    {hasContent ? `${item.files} 个 · ${formatBytes(item.bytes)}` : "已干净"}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}

      <div className="storage-actions">
        <button
          type="button"
          className="storage-select-all"
          onClick={() =>
            setSelected(allSelected ? new Set() : new Set(cleanable.map((item) => item.key)))
          }
          disabled={busy || cleanable.length === 0}
        >
          {allSelected ? "全不选" : "全选"}
        </button>
        <button
          type="button"
          onClick={() => void sweep()}
          disabled={busy || selectedCleanable.length === 0}
        >
          {sweeping
            ? "清理中…"
            : selectedCleanable.length > 0
              ? `清理选中项（${formatBytes(selectedBytes)}）`
              : "清理选中项"}
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
