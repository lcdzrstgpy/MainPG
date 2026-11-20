import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { temuPublishApi } from "../api/temuPublishApi";
import { useAppConfirm } from "./ConfirmDialog";
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
  classId,
  shopId,
  shopName,
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
  /** 尺码分类 ID（size-charts 响应的 class_id）：实验性建表要用 */
  classId: number;
  /** 当前店铺（尺码表模板是店铺级，ID 跨店不能混用）：新模板记归属、列表标差异 */
  shopId: string;
  shopName: string;
  /** 类目测量项（sizecharts.meta.get 的 elements，带 id）：列候选 + 建表元数据。
   * type=1 商品元素（进 meta/records），type=2 身体元素（进 bodyMeta/bodyRecords） */
  categoryElements: Array<{ id: number; name: string; type?: string }>;
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
  /** 实验性建表的状态/结果提示 */
  const [creating, setCreating] = useState("");
  const elementNames = categoryElements.map((item) => item.name);
  const [confirmNode, confirm] = useAppConfirm();

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
    const columns = elementNames.length
      ? elementNames.filter((el) => requiredElements.includes(el) || elementNames.indexOf(el) < 3)
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
      shopId: shopId || editing.shopId || "",
      shopName: shopName || editing.shopName || "",
      savedAt: Date.now(),
    };
    setTemplates(upsertSizeChartTemplate(template));
    setEditing(null);
  };

  /**
   * 实验性：直接在 Temu 建表（sizecharts.create，参数按 get 返回镜像）。
   * 成功 → 自动填模板 ID；被拒 → 网关原话亮出来（拿真实样例再对齐参数）。
   */
  const createOnTemu = async () => {
    if (!editing || creating) return;
    const name = editing.name.trim() || `Size Chart ${new Date().toISOString().slice(0, 10)}`;
    // 行列 → contentVO：groups 第一行是尺码列（id=1）；测量列按类型分流 ——
    // 商品元素（type=1）进 meta/records，身体元素（type=2）进 bodyMeta/bodyRecords
    const selected = editing.columns
      .map((column) => categoryElements.find((item) => item.name === column))
      .filter((item): item is { id: number; name: string; type?: string } => Boolean(item));
    if (!selected.length) {
      setCreating("测量列必须来自类目测量项（自定义列建不了表）");
      return;
    }
    const isBody = (item: { type?: string }) => String(item.type ?? "1") === "2";
    const productElements = selected.filter((item) => !isBody(item));
    const bodyElements = selected.filter(isBody);
    const groups = [{ id: 1, name: "size" }];
    const rowFor = (elements: typeof selected, row: SizeChartRow) => {
      const record: Record<string, string> = { "1": row.size };
      elements.forEach((element) => {
        record[String(element.id)] = row.values[editing.columns.indexOf(element.name)] ?? "";
      });
      return record;
    };
    setCreating("正在向 Temu 建表…");
    try {
      const result = await temuPublishApi.createSizeChart({
        name,
        class_id: classId,
        groups,
        elements: productElements.map((item) => ({ id: item.id, name: item.name })),
        records: editing.rows.map((row) => rowFor(productElements, row)),
        body_elements: bodyElements.map((item) => ({ id: item.id, name: item.name })),
        body_records: bodyElements.length
          ? editing.rows.map((row) => rowFor(bodyElements, row))
          : [],
      });
      if (result.id > 0) {
        // 建完直接就用：不用再点「用这张」，表单立刻拿到模板 ID（存档可选）
        const next = selectedIds.includes(result.id) ? selectedIds : [...selectedIds, result.id];
        onApply(next.join(","));
        setEditing({ ...editing, name, templateId: String(result.id) });
        setCreating(`✅ 已在 Temu 建表（模板 ID ${result.id}），已自动选用；点「保存模板」可存进模板库`);
      } else {
        setCreating("Temu 接受了请求但没回模板 ID，去卖家后台「尺码表模板」确认后手动填 ID");
      }
    } catch (cause) {
      setCreating(`建表被拒：${cause instanceof Error ? cause.message : String(cause)}`);
    }
  };

  const applyTemplate = (template: SizeChartTemplate) => {
    const id = Number(template.templateId.trim());
    if (!Number.isFinite(id) || id <= 0) return;
    const next = selectedIds.includes(id) ? selectedIds : [...selectedIds, id];
    onApply(next.join(","));
  };

  return createPortal(
    <>
      {confirmNode}
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
                  {elementNames.length
                    ? "按类目要求给出；带 * 的是必填项"
                    : "认不出类目测量项，先用常用预设"}
                </em>
              </span>
              <div className="temu-sizechart-columns">
                {(elementNames.length ? elementNames : SIZE_CHART_PRESETS[0].columns).map(
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
                        {template.shopId && shopId && template.shopId !== shopId ? (
                          <span className="temu-sizechart-shop-warn">
                            ⚠ 这张表建在「{template.shopName || template.shopId}」，
                            当前是「{shopName || shopId}」——尺码表 ID 跨店不能混用
                          </span>
                        ) : (
                          <span className="temu-sizechart-preview">
                            {template.columns.join(" / ")}
                          </span>
                        )}
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
                          onClick={async () => {
                            const ok = await confirm({
                              title: "删除模板？",
                              message: `删除「${template.name || "（未命名）"}」（ID ${template.templateId || "—"}）？\n只是删本机存档，Temu 店里建的表不受影响。`,
                              confirmText: "删除",
                              danger: true,
                            });
                            if (ok) setTemplates(removeSizeChartTemplate(template.id));
                          }}
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

        <footer className="temu-edit-foot temu-sizechart-foot">
          {editing ? (
            <>
              {creating ? <p className="temu-muted temu-sizechart-create-note">{creating}</p> : null}
              <button type="button" className="temu-btn" onClick={() => setEditing(null)}>
                返回列表
              </button>
              <button
                type="button"
                className="temu-btn temu-btn-sm"
                disabled={Boolean(creating) || !classId}
                title={
                  classId
                    ? "实验功能：按上方表格内容在 Temu 创建尺码表，创建成功后自动填入模板 ID（无需前往卖家后台手动创建）"
                    : "认不出尺码分类 ID，只能去卖家后台建"
                }
                onClick={() => void createOnTemu()}
              >
                在 Temu 建表（实验）
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
    </div>
    </>,
    document.body,
  );
}
