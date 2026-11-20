import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  backupSummaryText,
  exportTemuPublishBackup,
  importTemuPublishBackup,
} from "../data/backupStore";

/**
 * 产品模板 / 变种模板的「保存」「载入」弹窗。
 *
 * 以前是发布页里两个 `载入 / 保存 XX 模板` 折叠块，保存和载入混在一起、
 * 还共用一个输入框，容易搞混。现在拆成两个独立按钮，各弹自己的窗。
 *
 * 两个弹窗都挂在 body 上（和页面其它弹窗一致），Esc 关闭。
 */

export type TemplateItem = { name: string; savedAt?: string };

type SaveProps = {
  title: string;
  /** 预填的名字（类目名 / 规格组合），用户可改 */
  defaultName: string;
  hint?: string;
  /** 已有模板名，用于提示「同名会覆盖」 */
  existing: string[];
  onSave: (name: string) => void;
  onClose: () => void;
};

export function TemplateSaveDialog({
  title,
  defaultName,
  hint,
  existing,
  onSave,
  onClose,
}: SaveProps) {
  const [name, setName] = useState(defaultName);
  const trimmed = name.trim();
  const willOverwrite = !!trimmed && existing.includes(trimmed);

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
        className="temu-edit-dialog temu-template-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>{title}</strong>
            <span>{hint || "只存本机，换台机器就没有了"}</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <label className="temu-field">
          <span>模板名</span>
          <input
            autoFocus
            className="temu-input"
            value={name}
            placeholder="给这份模板起个名字，之后按名字载入"
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && trimmed) onSave(trimmed);
            }}
          />
        </label>
        {willOverwrite ? (
          <p className="temu-template-warn">已有同名模板，保存会覆盖它。</p>
        ) : null}

        <div className="temu-edit-foot">
          <button type="button" className="temu-btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="temu-btn temu-btn-primary"
            disabled={!trimmed}
            onClick={() => onSave(trimmed)}
          >
            保存
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

type LoadProps = {
  title: string;
  items: TemplateItem[];
  /** 没有模板时的一句话（告诉用户去哪存） */
  emptyHint: string;
  onPick: (name: string) => void;
  onRemove?: (name: string) => void;
  onClose: () => void;
};

export function TemplateLoadDialog({
  title,
  items,
  emptyHint,
  onPick,
  onRemove,
  onClose,
  onImported,
}: LoadProps & { onImported?: () => void }) {
  /** 备份文件的隐藏输入；按钮走它打开系统文件选择 */
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [backupNote, setBackupNote] = useState("");
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
        className="temu-edit-dialog temu-template-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>{title}</strong>
            <span>点击任一模板即可套用；仅填充空白项，已填写内容不会被覆盖</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        {items.length ? (
          <ul className="temu-template-list">
            {items.map((item) => (
              <li key={item.name}>
                <button type="button" className="temu-template-pick" onClick={() => onPick(item.name)}>
                  <strong>{item.name}</strong>
                  {item.savedAt ? <span>{item.savedAt}</span> : null}
                </button>
                {onRemove ? (
                  <button
                    type="button"
                    className="temu-template-del"
                    title="删除这个模板"
                    onClick={() => onRemove(item.name)}
                  >
                    ×
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="temu-template-empty">{emptyHint}</p>
        )}

        {backupNote ? (
          <p className="temu-muted temu-template-backup-note">{backupNote}</p>
        ) : null}

        <div className="temu-edit-foot">
          <button
            type="button"
            className="temu-btn temu-btn-sm"
            title="把产品模板、变种模板、属性估填、失败台账、尺码表模板打包成一个文件（可发给同事或带到新电脑）"
            onClick={() => {
              const { blob, filename, counts } = exportTemuPublishBackup();
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = filename;
              anchor.click();
              URL.revokeObjectURL(url);
              setBackupNote(`已导出：${backupSummaryText(counts)}`);
            }}
          >
            导出备份
          </button>
          <button
            type="button"
            className="temu-btn temu-btn-sm"
            title="导入备份文件；同名模板以文件里的为准，其余保留"
            onClick={() => importInputRef.current?.click()}
          >
            导入备份
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            style={{ display: "none" }}
            onChange={async (event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              try {
                const summary = await importTemuPublishBackup(file);
                setBackupNote(`已导入：${backupSummaryText(summary.merged)}（同名以文件为准）`);
                onImported?.();
              } catch (cause) {
                setBackupNote(cause instanceof Error ? cause.message : "导入失败，请检查文件");
              }
            }}
          />
          <button type="button" className="temu-btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
