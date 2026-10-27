import { useEffect, useRef, useState } from "react";

import type { TemuDraftEdit } from "../types";

export type BulkField = keyof TemuDraftEdit;

type MenuKey = "ops" | "attrs" | "category" | null;

/** 全属性修改面板的字段分组（按平台属性的下拉结构）。 */
const ATTR_GROUPS: Array<{
  title: string;
  items: Array<{ label: string; field?: BulkField; hint?: string }>;
}> = [
  {
    title: "常用项",
    items: [
      { label: "库存", field: "stock" },
      { label: "申报价格", field: "declared_price" },
      { label: "重量", field: "weight_g" },
    ],
  },
  {
    title: "产品信息",
    items: [
      { label: "产品标题", field: "title" },
      { label: "SKU 货号", field: "sku" },
      { label: "英文标题", hint: "草稿只有一份标题" },
      { label: "产品货号", field: "sku" },
      { label: "产地", hint: "草稿无此字段" },
      { label: "敏感属性", hint: "未接" },
    ],
  },
  {
    title: "变种信息",
    items: [
      { label: "建议零售价", field: "suggested_price" },
      { label: "长 (cm)", field: "length_cm" },
      { label: "宽 (cm)", field: "width_cm" },
      { label: "高 (cm)", field: "height_cm" },
      { label: "SKU 分类", hint: "未接" },
      { label: "仓库", hint: "发布页选" },
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

const OPS_SECTIONS: Array<{
  title: string;
  items: Array<{ label: string; action: "publish" | "edit" | "remark" | "delete" | "template" | "variantTemplate"; enabled?: boolean }>;
}> = [
  {
    title: "批量操作",
    items: [
      { label: "批量发布", action: "publish", enabled: true },
      { label: "批量备注", action: "remark", enabled: true },
      { label: "批量删除", action: "delete", enabled: true },
    ],
  },
  {
    title: "产品编辑",
    items: [{ label: "批量编辑", action: "edit", enabled: true }],
  },
  {
    title: "批量模板应用",
    items: [
      { label: "引用产品模板", action: "template", enabled: true },
      { label: "引用变种模板", action: "variantTemplate", enabled: true },
    ],
  },
];

type Props = {
  selectedCount: number;
  totalCount: number;
  hiddenCount: number;
  categories: string[];
  onApplyField: (field: BulkField, value: string) => void;
  onRemark: () => void;
  onDelete: () => void;
  onMoveCategory: (category: string) => void;
  onAddCategory: (category: string) => void;
  onRestoreHidden: () => void;
  onCreateProduct: () => void;
  /** 有产品模板才能批量发布；没有模板时提示先存模板 */
  canBatchPublish?: boolean;
  /** 上一批还在提交：禁止再开一批（接口非幂等） */
  batchRunning?: boolean;
  templateCount?: number;
  onBatchPublish: () => void;
  onPickTemplate: (kind: "product" | "variant") => void;
};

export function TemuDraftBulkBar({
  selectedCount,
  totalCount,
  hiddenCount,
  categories,
  onApplyField,
  onRemark,
  onDelete,
  onMoveCategory,
  onAddCategory,
  onRestoreHidden,
  onCreateProduct,
  canBatchPublish = false,
  batchRunning = false,
  templateCount = 0,
  onBatchPublish,
  onPickTemplate,
}: Props) {
  const [menu, setMenu] = useState<MenuKey>(null);
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

  const toggle = (key: MenuKey) => {
    setMenu((current) => (current === key ? null : key));
    setField(null);
    setValue("");
  };

  const apply = () => {
    if (!field || !value.trim()) return;
    onApplyField(field, value.trim());
    setField(null);
    setValue("");
    setMenu(null);
  };

  const filteredCategories = categories.filter((item) =>
    categoryQuery.trim() ? item.includes(categoryQuery.trim()) : true,
  );

  return (
    <div className="temu-bulk-bar" ref={rootRef}>
      <div className="temu-bulk-left">
        <div className="temu-bulk-menu-wrap">
          <button
            type="button"
            className={`temu-bulk-trigger${menu === "ops" ? " is-open" : ""}`}
            onClick={() => toggle("ops")}
          >
            批量操作 <span aria-hidden="true">▾</span>
          </button>
          {menu === "ops" && (
            <div className="temu-bulk-panel is-ops">
              {OPS_SECTIONS.map((section) => (
                <div className="temu-bulk-section" key={section.title}>
                  <p>{section.title}</p>
                  <div className="temu-bulk-items">
                    {section.items.map((item) => (
                      <button
                        key={`${section.title}-${item.label}`}
                        type="button"
                        disabled={
                          !item.enabled ||
                          (item.action === "publish" && (batchRunning || !selectedCount)) ||
                          (item.action !== "publish" && selectedCount === 0)
                        }
                        title={
                          !item.enabled
                            ? "这一项还没接数据，未开放"
                            : batchRunning
                              ? "上一批还在提交，等它跑完再发"
                              : selectedCount === 0
                              ? "先勾选左侧复选框"
                              : item.action === "publish" && !canBatchPublish
                                ? "点击后在弹窗里选产品模板（批量发布用模板里的类目和属性）"
                                : item.action !== "publish" && templateCount === 0
                                  ? "还没有模板：去发布页「存为产品模板 / 变种模板」"
                                  : undefined
                        }
                        onClick={() => {
                          if (item.action === "remark") onRemark();
                          else if (item.action === "delete") onDelete();
                          else if (item.action === "edit") setMenu("attrs");
                          else if (item.action === "publish") onBatchPublish();
                          else onPickTemplate(item.action === "template" ? "product" : "variant");
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <p className="temu-bulk-tip">
                {selectedCount > 0 ? `已选 ${selectedCount} / ${totalCount} 条` : "先勾选要处理的产品"}
              </p>
            </div>
          )}
        </div>

        <div className="temu-bulk-menu-wrap">
          <button
            type="button"
            className={`temu-bulk-trigger${menu === "attrs" ? " is-open" : ""}`}
            onClick={() => toggle("attrs")}
          >
            全属性修改 <span aria-hidden="true">▾</span>
          </button>
          {menu === "attrs" && (
            <div className="temu-bulk-panel is-attrs">
              {ATTR_GROUPS.map((group) => (
                <div className="temu-bulk-section" key={group.title}>
                  <p>{group.title}</p>
                  <div className="temu-bulk-items">
                    {group.items.map((item) => (
                      <button
                        key={`${group.title}-${item.label}`}
                        type="button"
                        className={field === item.field && item.field ? "is-picked" : ""}
                        disabled={!item.field || selectedCount === 0}
                        title={
                          item.hint || (selectedCount === 0 ? "先勾选左侧复选框" : undefined)
                        }
                        onClick={() => {
                          if (!item.field) return;
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
            </div>
          )}
        </div>

        <div className="temu-bulk-menu-wrap">
          <button
            type="button"
            className={`temu-bulk-trigger${menu === "category" ? " is-open" : ""}`}
            onClick={() => toggle("category")}
          >
            移动分类 <span aria-hidden="true">▾</span>
          </button>
          {menu === "category" && (
            <div className="temu-bulk-panel is-category">
              <input
                className="temu-bulk-search"
                value={categoryQuery}
                onChange={(event) => setCategoryQuery(event.target.value)}
                placeholder="搜索产品分类"
              />
              <div className="temu-bulk-cats">
                <button type="button" disabled={selectedCount === 0} onClick={() => onMoveCategory("")}>
                  未分类
                </button>
                {filteredCategories.map((item) => (
                  <button
                    key={item}
                    type="button"
                    disabled={selectedCount === 0}
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
            </div>
          )}
        </div>

        {hiddenCount > 0 && (
          <button type="button" className="temu-bulk-link" onClick={onRestoreHidden}>
            已隐藏 {hiddenCount} 条 · 恢复
          </button>
        )}
      </div>

      <div className="temu-bulk-right">
        <button type="button" className="temu-btn temu-btn-primary" onClick={onCreateProduct}>
          创建产品
        </button>
      </div>
    </div>
  );
}
