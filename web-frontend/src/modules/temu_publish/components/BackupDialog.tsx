import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  backupSummaryText,
  exportTemuPublishBackup,
  importTemuPublishBackup,
} from "../data/backupStore";

/**
 * 数据备份弹窗：一个入口「导出 / 导入备份」，点开先说清楚备份是什么、
 * 再给两个动作按钮。直接在页头摆两个裸按钮会让人不知道是什么就点下去
 * （2026-11-20 用户反馈）。
 */
export function BackupDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  /** 导入成功后调用：页面刷新模板与列表 */
  onImported?: () => void;
}) {
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="temu-edit-backdrop" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-backup-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="导出 / 导入备份"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>导出 / 导入备份</strong>
            <span>把本机保存的配置与记录打包带走，或从备份文件恢复</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-backup-body">
          <p>
            产品模板、变种模板、属性估填、失败台账、尺码表模板保存在本机浏览器中；
            换电脑、重装或清理浏览器缓存后会丢失。
          </p>
          <p>
            <strong>导出</strong>：打包成一个文件，可发给同事或带到新电脑；
            <strong>导入</strong>：选择备份文件后恢复。导入不会清掉已有数据——
            同名模板以文件里的为准，其余保留。
          </p>
          {note ? (
            <p className="temu-backup-note" role="status" aria-live="polite">
              {note}
            </p>
          ) : null}
        </div>

        <footer className="temu-edit-foot">
          <button
            type="button"
            className="temu-btn temu-btn-primary"
            onClick={() => {
              const { blob, filename, counts } = exportTemuPublishBackup();
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = filename;
              anchor.click();
              URL.revokeObjectURL(url);
              setNote(`已导出：${backupSummaryText(counts)}`);
            }}
          >
            导出备份
          </button>
          <button
            type="button"
            className="temu-btn"
            onClick={() => importInputRef.current?.click()}
          >
            导入备份
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            onChange={async (event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              try {
                const summary = await importTemuPublishBackup(file);
                setNote(`已导入：${backupSummaryText(summary.merged)}（同名以文件为准）`);
                onImported?.();
              } catch (cause) {
                setNote(cause instanceof Error ? cause.message : "导入失败，请检查文件");
              }
            }}
          />
          <button type="button" className="temu-btn" onClick={onClose}>
            关闭
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
