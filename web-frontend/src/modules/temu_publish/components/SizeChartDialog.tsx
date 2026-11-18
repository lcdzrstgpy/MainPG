import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

import {
  draftRowsFromSizes,
  loadSizeChartTemplates,
  removeSizeChartTemplate,
  SIZE_CHART_PRESETS,
  upsertSizeChartTemplate,
  type SizeChartRow,
  type SizeChartTemplate,
} from "../data/sizeChartStore";

/**
 * 尺码表模板库弹窗：选模板 / 建模板。
 *
 * 「选」填的是 Temu 模板 ID（提交时唯一用到的东西）；行列格子是备忘 + 建表辅助 ——
 * 在这里把表拼好（行=本商品的码、列=类目要求的测量项），去卖家后台照着建一张，
 * 把后台给的模板 ID 存回来，下次全组商品直接选。
 */
export function SizeChartDialog({
  open,
  onClose,
  catId,
  categoryElements,
  requiredElements,
  categorySizes,
  initialSizes,
  currentIds,
  onApply,
}: {
  open: boolean;
  onClose: () => void;
  /** 当前叶子类目：用于「适用类目」标记和测量项/尺码候选 */
  catId: number;
  /** 类目测量项（sizecharts.meta.get 的 elements）：列候选 */
  categoryElements: string[];
  /** 类目必填测量项：进编辑器默认勾上且不可取消 */
  requiredElements: string[];
  /** 类目可选尺码名（sizecharts.settings.get）：行候选 */
  categorySizes: string[];
  /** 本商品变种里的尺码值（sizesText）：建表时优先用它出行 */
  initialSizes: string[];
  /** 当前已填的模板 ID 串（form.sizeTemplates） */
  currentIds: string;
  /** 应用：把新的模板 ID 串写回表单 */
  onApply: (ids: string) => void;
}) {
  const [templates, setTemplates] = useState<SizeChartTemplate[]>(() => loadSizeChartTemplates());
  const [editing, setEditing] = useState<SizeChartTemplate | null>(null);
  /** 编辑器里尺码行的原始输入（逗号分隔，落表时拆行） */
  const [rowSizesText, setRowSizesText] = useState("");

  const selectedIds = useMemo(
    () =>
      currentIds
        .split(/[,，\s]+/)
        .map((item) => Number(item))
        .filter((id) => Number.isFinite(id) && id > 0),
    [currentIds],
  );

  // 适用类目的排前面：这个类目用过的表不用翻全库
  const sorted = useMemo(() => {
    const matched = templates.filter((item) => item.catIds.includes(catId));
    const rest = templates.filter((item) => !item.catIds.includes(catId));
    return [...matched, ...rest];
  }, [templates, catId]);

  if (!open) return null;

  const startCreate = () => {
    const columns = categoryElements.length
      ? categoryElements.filter((el) => requiredElements.includes(el) || categoryElements.indexOf(el) < 3)
      : SIZE_CHART_PRESETS[0].columns;
    const sizes = initialSizes.length ? initialSizes : categorySizes;
    setEditing({
      id: `sc:${Date.now().toString(36)}`,
      name: "",
      templateId: "",
      catIds: catId > 0 ? [catId] : [],
      columns,
      rows: draftRowsFromSizes(sizes, columns.length),
      savedAt: 0,
    });
    setRowSizesText(sizes.join(", "));
  };

  const rebuildRows = (nextSizesText: string, columns: string[]) => {
    const sizes = nextSizesText.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
    setEditing((current) => {
      if (!current) return current;
      // 已填的值按尺码名保留，新尺码补空行
      const old = new Map(current.rows.map((row) => [row.size, row.values]));
      return {
        ...current,
        rows: sizes.map((size) => ({
          size,
          values: columns.map((_, index) => old.get(size)?.[index] ?? ""),
        })),
      };
    });
  };

  const toggleColumn = (column: string) => {
    if (requiredElements.includes(column)) return;
    setEditing((current) => {
      if (!current) return current;
      const columns = current.columns.includes(column)
        ? current.columns.filter((item) => item !== column)
        : [...current.columns, column];
      return {
        ...current,
        columns,
        rows: current.rows.map((row) => ({
          ...row,
          values: columns.map((_, index) => row.values[index] ?? ""),
        })),
      };
    });
  };

  const setCell = (rowIndex: number, columnIndex: number, value: string) => {
    setEditing((current) => {
      if (!current) return current;
      return {
        ...current,
        rows: current.rows.map((row, index) =>
          index === rowIndex
            ? { ...row, values: row.values.map((cell, c) => (c === columnIndex ? value : cell)) }
            : row,
        ),
      };
    });
  };

  const save = () => {
    if (!editing) return;
    const template: SizeChartTemplate = {
      ...editing,
      name: editing.name.trim() || `尺码表 ${new Date().toLocaleDateString()}`,
      savedAt: Date.now(),
    };
    setTemplates(upsertSizeChartTemplate(template));
    setEditing(null);
  };

  const applyTemplate = (template: SizeChartTemplate) => {
    const id = Number(template.templateId.trim());
    if (!Number.isFinite(id) || id <= 0) return;
    const next = selectedIds.includes(id) ? selectedIds : [...selectedIds, id];
    onApply(next.join(","));
  };

  return createPortal(
    <div className="temu-edit-backdrop" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-sizechart-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="尺码表模板库"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>尺码表模板库</strong>
            <span>选一张填 ID；没有就新建（拼好格子去后台照建，ID 存回来一劳永逸）</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        {editing ? (
          <div className="temu-edit-body temu-sizechart-editor">
            <label className="temu-field">
              <span>模板名称</span>
              <input
                className="temu-input"
                autoFocus
                value={editing.name}
                placeholder="例如：女装上衣-常规"
                onChange={(event) => setEditing({ ...editing, name: event.target.value })}
              />
            </label>
            <label className="temu-field">
              <span>
                Temu 模板 ID<em className="temu-required">*</em>
                <em className="temu-muted">在卖家后台「商品管理 → 尺码表模板」建表后抄来</em>
              </span>
              <input
                className="temu-input"
                inputMode="numeric"
                value={editing.templateId}
                placeholder="例如：9527"
                onChange={(event) => setEditing({ ...editing, templateId: event.target.value })}
              />
            </label>

            <div className="temu-field">
              <span>
                测量列
                <em className="temu-muted">
                  {categoryElements.length
                    ? "按类目要求给出；带 * 的是必填项"
                    : "认不出类目测量项，先用常用预设"}
                </em>
              </span>
              <div className="temu-sizechart-columns">
                {(categoryElements.length ? categoryElements : SIZE_CHART_PRESETS[0].columns).map(
                  (column) => {
                    const on = editing.columns.includes(column);
                    const locked = requiredElements.includes(column);
                    return (
                      <button
                        key={column}
                        type="button"
                        className={`temu-sizechart-chip${on ? " is-on" : ""}${locked ? " is-locked" : ""}`}
                        title={locked ? "类目必填测量项，不能取消" : on ? "点击取消" : "点击勾选"}
                        onClick={() => toggleColumn(column)}
                      >
                        {column}
                        {locked ? <em>*</em> : null}
                      </button>
                    );
                  },
                )}
              </div>
            </div>

            <label className="temu-field">
              <span>
                尺码行
                <em className="temu-muted">逗号分隔；默认带出本商品变种的尺码</em>
              </span>
              <input
                className="temu-input"
                value={rowSizesText}
                placeholder="S, M, L, XL"
                onChange={(event) => {
                  setRowSizesText(event.target.value);
                  rebuildRows(event.target.value, editing.columns);
                }}
              />
            </label>

            <table className="temu-sizechart-grid">
              <thead>
                <tr>
                  <th>尺码</th>
                  {editing.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {editing.rows.map((row, rowIndex) => (
                  <tr key={row.size}>
                    <td>
                      <strong>{row.size}</strong>
                    </td>
                    {editing.columns.map((column, columnIndex) => (
                      <td key={column}>
                        <input
                          className="temu-batch-edit-input"
                          inputMode="decimal"
                          value={row.values[columnIndex] ?? ""}
                          onChange={(event) => setCell(rowIndex, columnIndex, event.target.value)}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
                {!editing.rows.length ? (
                  <tr>
                    <td className="temu-muted" colSpan={editing.columns.length + 1}>
                      先填尺码行（S, M, L…），格子就出来了
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="temu-edit-body">
            {sorted.length ? (
              <ul className="temu-sizechart-list">
                {sorted.map((template) => {
                  const id = Number(template.templateId.trim());
                  const picked = Number.isFinite(id) && selectedIds.includes(id);
                  return (
                    <li key={template.id} className={picked ? "is-on" : ""}>
                      <div>
                        <strong>{template.name || "（未命名）"}</strong>
                        <span className="temu-muted">
                          ID {template.templateId || "—"} · {template.columns.length} 列 ×{" "}
                          {template.rows.length} 码
                          {template.catIds.includes(catId) ? " · 适合本类目" : ""}
                        </span>
                        <span className="temu-sizechart-preview">
                          {template.columns.join(" / ")}
                        </span>
                      </div>
                      <div className="temu-sizechart-actions">
                        <button
                          type="button"
                          className="temu-btn temu-btn-sm temu-btn-primary"
                          onClick={() => applyTemplate(template)}
                        >
                          {picked ? "已选用" : "用这张"}
                        </button>
                        <button
                          type="button"
                          className="temu-btn temu-btn-sm"
                          onClick={() => {
                            setEditing(template);
                            setRowSizesText(template.rows.map((row) => row.size).join(", "));
                          }}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          className="temu-btn temu-btn-sm"
                          onClick={() => setTemplates(removeSizeChartTemplate(template.id))}
                        >
                          删除
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="temu-muted">还没有模板。点「新建模板」拼一张：行=商品尺码、列=测量项。</p>
            )}
          </div>
        )}

        <footer className="temu-edit-foot">
          {editing ? (
            <>
              <button type="button" className="temu-btn" onClick={() => setEditing(null)}>
                返回列表
              </button>
              <button
                type="button"
                className="temu-btn temu-btn-primary"
                disabled={!editing.templateId.trim()}
                onClick={save}
              >
                保存模板
              </button>
            </>
          ) : (
            <>
              <button type="button" className="temu-btn" onClick={onClose}>
                关闭
              </button>
              <button type="button" className="temu-btn temu-btn-primary" onClick={startCreate}>
                新建模板
              </button>
            </>
          )}
        </footer>
      </div>
    </div>,
    document.body,
  );
}
