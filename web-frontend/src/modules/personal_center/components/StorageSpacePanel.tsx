import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import {
  scanPodCache,
  scanProductProcessingCache,
  sweepPodCache,
  sweepProductProcessingCache,
  type PodCacheScanCategory,
} from "../api/personalCenterApi";
import { StorageSweepConfirmDialog } from "./StorageSweepConfirmDialog";
import { computeScanSelection } from "./storageSelection";

/** 扫描结果的来源（两套缓存各有独立后端与独立分组）。 */
type CacheSource = "pod" | "pp";
type StorageCategoryItem = PodCacheScanCategory & { source: CacheSource };

const SOURCE_TITLE: Record<CacheSource, string> = {
  pod: "POD 定制缓存",
  pp: "产品处理缓存",
};

/** 分类键 → 展示文案（后端返回未知键时回退为原键名）。 */
const CATEGORY_META: Record<string, { label: string; hint: string }> = {
  // —— POD 定制（后端 key 无前缀，保持兼容）——
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
  // —— 产品处理 ——
  pp_asset_orphans: {
    label: "媒体库无引用图片",
    hint: "已无任何草稿、任务引用的图片",
  },
  pp_disk_orphans: {
    label: "无引用遗留文件",
    hint: "磁盘上没有记录认领的遗留文件（含任务结果引用的复核原图会保留）",
  },
  pp_review_images: {
    label: "历史任务的复核原图",
    hint: "任务失败时留档的供应商原始图，超过 30 天可清理（删除后任务记录保留，但原图不再可查）",
  },
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/**
 * 存储空间：先扫描、再勾选清理本机缓存。
 *
 * 覆盖两块互相独立的缓存（各自单独的后端接口，互不影响）：
 * - POD 定制（批次图片、图床备份副本、孤儿文件）；
 * - 产品处理（媒体库无引用图片、无引用遗留文件）。
 *
 * 两套后端都只清「没有任何记录引用的本地文件」：模板、在用资产、被其它记录
 * 共享的文件都有完整护栏。唯一例外是「历史任务的复核原图」分类（默认不勾）：
 * 它按用户显式勾选清理超 30 天的留档原图，任务记录本身不动。**未勾选的分类
 * 一步都不会执行**；重复清理是幂等的。
 */
export function StorageSpacePanel() {
  const [categories, setCategories] = useState<StorageCategoryItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scanning, setScanning] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [scanError, setScanError] = useState("");
  const [lastFreed, setLastFreed] = useState<{ files: number; bytes: number } | null>(null);
  // 清理前必须先过确认弹窗（不可恢复分类给红色警示）；打开时冻结勾选快照。
  const [confirmSelection, setConfirmSelection] = useState<StorageCategoryItem[] | null>(null);
  const busyRef = useRef(false);
  const seenScanRef = useRef(false);

  const scan = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setScanning(true);
    setScanError("");
    try {
      // 两块缓存独立扫描：一边失败（如旧版本后端没有该接口）不影响另一边展示。
      const [podResult, ppResult] = await Promise.allSettled([
        scanPodCache(),
        scanProductProcessingCache(),
      ]);
      const items: StorageCategoryItem[] = [];
      if (podResult.status === "fulfilled") {
        items.push(
          ...podResult.value.categories.map((item) => ({ ...item, source: "pod" as const })),
        );
      }
      if (ppResult.status === "fulfilled") {
        items.push(
          ...ppResult.value.categories.map((item) => ({ ...item, source: "pp" as const })),
        );
      }
      if (items.length === 0) {
        const firstError =
          podResult.status === "rejected"
            ? podResult.reason
            : ppResult.status === "rejected"
              ? ppResult.reason
              : null;
        throw firstError instanceof Error ? firstError : new Error("扫描失败，请稍后重试");
      }
      setCategories(items);
      const failures = [podResult, ppResult]
        .filter((result): result is PromiseRejectedResult => result.status === "rejected")
        .map((result) => (result.reason instanceof Error ? result.reason.message : "扫描失败"));
      setScanError(failures.length ? `部分缓存扫描失败：${failures.join("；")}` : "");
      // ⚠️ 必须在同步代码里先定「是否首次」：updater 延迟执行，内部读 ref 会永远是 false。
      const firstScan = !seenScanRef.current;
      seenScanRef.current = true;
      setSelected((previous) => computeScanSelection(items, previous, firstScan));
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

  const groups: Array<{ source: CacheSource; items: StorageCategoryItem[] }> = [];
  for (const item of categories) {
    const group = groups.find((entry) => entry.source === item.source);
    if (group) group.items.push(item);
    else groups.push({ source: item.source, items: [item] });
  }

  const sweep = async () => {
    if (selectedCleanable.length === 0) {
      showToast("请先勾选要清理的项目", "info");
      return;
    }
    // 先弹确认（列出清单与总量；不可恢复分类给红色警示），确认后才真正执行。
    // 快照冻结在打开时刻，避免弹窗开着时后台刷新或勾选变化影响实际清理范围。
    setConfirmSelection([...selectedCleanable]);
  };

  const runSweep = async (items: StorageCategoryItem[]) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setSweeping(true);
    try {
      const podKeys = items.filter((item) => item.source === "pod").map((item) => item.key);
      const ppKeys = items.filter((item) => item.source === "pp").map((item) => item.key);
      const failures: string[] = [];
      let filesRemoved = 0;
      let bytesFreed = 0;
      if (podKeys.length) {
        try {
          const result = await sweepPodCache(podKeys);
          filesRemoved += result.files_removed;
          bytesFreed += result.bytes_freed;
        } catch (error) {
          failures.push(error instanceof Error ? error.message : "POD 缓存清理失败");
        }
      }
      if (ppKeys.length) {
        try {
          const result = await sweepProductProcessingCache(ppKeys);
          filesRemoved += result.files_removed;
          bytesFreed += result.bytes_freed;
        } catch (error) {
          failures.push(error instanceof Error ? error.message : "产品处理缓存清理失败");
        }
      }
      setLastFreed({ files: filesRemoved, bytes: bytesFreed });
      if (failures.length) {
        const prefix = filesRemoved > 0 ? `已释放 ${formatBytes(bytesFreed)}；` : "";
        showToast(`${prefix}部分清理失败：${failures.join("；")}`, "error");
      } else if (filesRemoved > 0) {
        showToast(`已释放 ${formatBytes(bytesFreed)}，共清理 ${filesRemoved} 个缓存文件`, "success");
      } else {
        showToast("没有可释放的空间；新产生的文件要超过保留期（48 小时）才会进入清理范围", "info");
      }
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
          <small>
            勾选要清理的项目释放磁盘空间；使用中的图片与模板不会被删除。仅清理超过 48
            小时的文件（复核原图为 30 天），刚产生的文件会等过了保留期才出现在这里。
          </small>
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

      {groups.map((group) => (
        <div key={group.source} className="storage-group">
          <p className="storage-group-title">{SOURCE_TITLE[group.source]}</p>
          <ul className="storage-category-list">
            {group.items.map((item) => {
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
                      {hasContent ? `${item.files} 个 · ${formatBytes(item.bytes)}` : "暂无可清理项"}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ))}

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

      {lastFreed && (
        <p className="storage-result" role="status">
          上次清理：释放 {formatBytes(lastFreed.bytes)}（{lastFreed.files} 个文件）
        </p>
      )}

      <StorageSweepConfirmDialog
        open={confirmSelection !== null}
        items={(confirmSelection ?? []).map((item) => ({
          key: item.key,
          label: (CATEGORY_META[item.key] ?? { label: item.key }).label,
          files: item.files,
          bytes: item.bytes,
        }))}
        onClose={() => setConfirmSelection(null)}
        onConfirm={() => {
          const snapshot = confirmSelection ?? [];
          setConfirmSelection(null);
          void runSweep(snapshot);
        }}
      />
    </article>
  );
}
