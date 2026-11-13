import { useEffect, useRef, useState } from "react";

import type { TemuDraftEdit } from "../types";

export type BulkField = keyof TemuDraftEdit;

type MenuKey = "panel" | null;
type View = "fields" | "category";

/**
 * 批量改字段：只列草稿真的有的字段。
 *
 * 以前这里还摆着「英文标题 / 产地 / 敏感属性 / SKU 分类 / 仓库」这些灰按钮 ——
 * 点不动、也没解释，纯粹是噪音。仓库/属性这类要在发布页按类目选，不属于草稿库。
 */
const FIELD_GROUPS: Array<{ title: string; fields: Array<{ label: string; field: BulkField }> }> = [
  {
    title: "价格与库存",
    fields: [
      { label: "申报价 (元)", field: "declared_price" },
      { label: "建议零售价 (元)", field: "suggested_price" },
      { label: "库存", field: "stock" },
    ],
  },
  {
    title: "商品信息",
    fields: [
      { label: "产品标题", field: "title" },
      { label: "SKU 货号", field: "sku" },
    ],
  },
  {
    title: "包装尺寸",
    fields: [
      { label: "重量 (g)", field: "weight_g" },
      { label: "长 (cm)", field: "length_cm" },
      { label: "宽 (cm)", field: "width_cm" },
      { label: "高 (cm)", field: "height_cm" },
    ],
  },
];

/** 字段英文 key → 界面中文名（批量设置那一行要显示人话）。 */
const FIELD_LABELS: Record<BulkField, string> = {
  title: "产品标题",
  sku: "SKU 货号",
  declared_price: "申报价格",
  suggested_price: "建议零售价",
  stock: "库存",
  weight_g: "重量 (g)",
  length_cm: "长 (cm)",
  width_cm: "宽 (cm)",
  height_cm: "高 (cm)",
};

type Props = {
  selectedCount: number;
  totalCount: number;
  hiddenCount: number;
  categories: string[];
  onApplyField: (field: BulkField, value: string) => void;
  /** 打开整页批量编辑（勾选的会带过去逐行改） */
  onBatchEdit: () => void;
  /** 打开整页批量编辑产品图片（对齐店小秘：删图/换首图/加图/超量移除） */
  onBatchImages: () => void;
  onRemark: () => void;
  onDelete: () => void;
  onMoveCategory: (category: string) => void;
  onAddCategory: (category: string) => void;
  onRestoreHidden: () => void;
  /** 有产品模板才能批量发布；没有模板时提示先存模板 */
  canBatchPublish?: boolean;
  /** 上一批还在提交：禁止再开一批（接口非幂等） */
  batchRunning?: boolean;
  templateCount?: number;
  onBatchPublish: () => void;
  onPickTemplate: (kind: "product" | "variant") => void;
  /** 选中态下的「取消选择」 */
  onClearSelection?: () => void;
};

/**
 * 草稿库的批量操作条。
 *
 * 两种形态：
 * - 没勾选：一个「批量操作」入口（打开面板看到全部能力），不占地方；
 * - 勾选了：直接变成一排快捷动作（发布 / 改价 / 改库存 / 移分类 / 备注 / 删除），
 *   常用操作不用再进菜单翻两层。三个并列下拉折成一个 —— 之前「批量操作」
 *   「全属性修改」「移动分类」互相嵌套，用户不知道该点哪个。
 */
export function TemuDraftBulkBar({
  selectedCount,
  totalCount,
  hiddenCount,
  categories,
  onApplyField,
  onBatchEdit,
  onBatchImages,
  onRemark,
  onDelete,
  onMoveCategory,
  onAddCategory,
  onRestoreHidden,
  canBatchPublish = false,
  batchRunning = false,
  templateCount = 0,
  onBatchPublish,
  onPickTemplate,
  onClearSelection,
}: Props) {
  const [menu, setMenu] = useState<MenuKey>(null);
  const [view, setView] = useState<View>("fields");
  const [field, setField] = useState<BulkField | null>(null);
  const [value, setValue] = useState("");
  const [categoryQuery, setCategoryQuery] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMenu(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  /** 打开面板并直接落到某个视图（快捷按钮用：点「改库存」就直接出输入框）。 */
  const openPanel = (next: View) => {
    setMenu("panel");
    setView(next);
    setField(null);
    setValue("");
  };

  const closePanel = () => {
    setMenu(null);
    setView("fields");
    setField(null);
    setValue("");
  };

  const apply = () => {
    if (!field || !value.trim()) return;
    onApplyField(field, value.trim());
    setField(null);
    setValue("");
    closePanel();
  };

  const filteredCategories = categories.filter((item) =>
    categoryQuery.trim() ? item.includes(categoryQuery.trim()) : true,
  );

  const hasSelection = selectedCount > 0;
  const disabledNoSelection = !hasSelection;

  return (
    <div className={`temu-bulk-bar${hasSelection ? " has-selection" : ""}`} ref={rootRef}>
      <div className="temu-bulk-left">
        {hasSelection ? (
          <>
            <span className="temu-bulk-count">
              已选 <b>{selectedCount}</b>
              {totalCount ? <em>共 {totalCount} 条</em> : <em>条</em>}
            </span>
            <button
              type="button"
              className="temu-bulk-action is-primary"
              disabled={batchRunning}
              title={
                batchRunning
                  ? "上一批还在提交，等它跑完再发"
                  : canBatchPublish
                    ? "逐条提交到店铺（弹窗里选模板）"
                    : "点一下：按每条草稿的类目分组，没存模板的用草稿属性自动拼（属性是估的，倒计时里可取消复核）"
              }
              onClick={onBatchPublish}
            >
              {batchRunning ? "发布中…" : "批量发布"}
            </button>
            <button
              type="button"
              className="temu-bulk-action"
              title="打开整页批量编辑：勾上要改的字段，逐行改完保存"
              onClick={onBatchEdit}
            >
              批量编辑
            </button>
            <button
              type="button"
              className="temu-bulk-action"
              title="批量编辑产品图片：删图 / 换首图 / 加图 / 超量移除"
              onClick={onBatchImages}
            >
              编辑图片
            </button>
            <button
              type="button"
              className="temu-bulk-action"
              disabled={batchRunning || disabledNoSelection}
              title={
                templateCount === 0
                  ? "还没有产品模板：去发布页「保存产品模板」"
                  : "套用已保存的产品模板（分类+属性+物流）"
              }
              onClick={() => onPickTemplate("product")}
            >
              引用产品模板
            </button>
            <button
              type="button"
              className="temu-bulk-action"
              disabled={batchRunning || disabledNoSelection}
              title={
                templateCount === 0
                  ? "还没有变种模板：去发布页「保存变种模板」"
                  : "套用已保存的变种模板（规格轴+价格+尺寸）"
              }
              onClick={() => onPickTemplate("variant")}
            >
              引用变种模板
            </button>
            <button
              type="button"
              className="temu-bulk-action"
              disabled={disabledNoSelection}
              title="选字段 → 填一个值 → 批量应用到勾选的草稿"
              onClick={() => openPanel("fields")}
            >
              批量改字段
            </button>
            <button type="button" className="temu-bulk-action" onClick={() => openPanel("category")}>
              移分类
            </button>
            <button type="button" className="temu-bulk-action" onClick={onRemark}>
              备注
            </button>
            <button
              type="button"
              className="temu-bulk-action is-danger"
              title="从成品库移除这些条目（不动 Temu 店铺里的商品）"
              onClick={onDelete}
            >
              删除
            </button>
            {onClearSelection ? (
              <button type="button" className="temu-bulk-link" onClick={onClearSelection}>
                取消选择
              </button>
            ) : null}
          </>
        ) : (
          <span className="temu-bulk-hint">勾选左侧复选框后，可批量发布 / 改字段 / 移分类</span>
        )}

        {hiddenCount > 0 && (
          <button type="button" className="temu-bulk-link" onClick={onRestoreHidden}>
            已隐藏 {hiddenCount} 条 · 恢复
          </button>
        )}
      </div>

      {menu === "panel" && (
        <div className="temu-bulk-panel">
          {view === "fields" && (
            <>
              {FIELD_GROUPS.map((group) => (
                <div className="temu-bulk-section" key={group.title}>
                  <p>{group.title}</p>
                  <div className="temu-bulk-items">
                    {group.fields.map((item) => (
                      <button
                        key={item.field}
                        type="button"
                        className={field === item.field ? "is-picked" : ""}
                        disabled={disabledNoSelection}
                        onClick={() => {
                          setField(item.field);
                          setValue("");
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {field && (
                <div className="temu-bulk-apply">
                  <span>
                    批量设置 <b>{FIELD_LABELS[field]}</b>
                  </span>
                  <input
                    autoFocus
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") apply();
                    }}
                    placeholder="输入要批量设置的值"
                  />
                  <button type="button" onClick={apply} disabled={!value.trim()}>
                    应用
                  </button>
                </div>
              )}
            </>
          )}

          {view === "category" && (
            <>
              <input
                className="temu-bulk-search"
                value={categoryQuery}
                onChange={(event) => setCategoryQuery(event.target.value)}
                placeholder="搜索产品分类"
              />
              <div className="temu-bulk-cats">
                <button type="button" disabled={disabledNoSelection} onClick={() => onMoveCategory("")}>
                  未分类
                </button>
                {filteredCategories.map((item) => (
                  <button
                    key={item}
                    type="button"
                    disabled={disabledNoSelection}
                    onClick={() => onMoveCategory(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="temu-bulk-addcat">
                <input
                  value={newCategory}
                  onChange={(event) => setNewCategory(event.target.value)}
                  placeholder="新建分类"
                />
                <button
                  type="button"
                  disabled={!newCategory.trim()}
                  onClick={() => {
                    onAddCategory(newCategory.trim());
                    setNewCategory("");
                  }}
                >
                  添加
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
