import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

/**
 * 单个列头的「批量改」弹窗。
 *
 * 设计约束（踩过的坑都在这）：
 *   · 表头里**不放输入框**：标签 + 规则按钮 + 输入框挤在一行必然互相压（截图里的惨状）。
 *     表头只放一个「批量改」小按钮，输入和规则全在弹窗里。
 *   · 三类字段三种规则：文字=直接改；数值=直接改 / 按现有价加减乘；SKU=序列规则。
 *   · 改完先给预览（before → after），撞号/负数这类硬错误直接禁用提交。
 */

export type ColumnKind = "text" | "number" | "sku";

type Row = { value: string; color?: string; size?: string };

type Props = {
  label: string;
  kind: ColumnKind;
  /** number 用：保留几位小数（价格 2 位，库存/尺寸 0 位） */
  decimals?: number;
  rows: Row[];
  onApply: (values: string[]) => void;
  onClose: () => void;
};

type NumberMode = "set" | "offset";
type Op = "add" | "sub" | "mul";

/** 生成一条货号：前缀 + 序号(补零) + 后缀 [+ -颜色-尺码] */
export function buildSkuCode(
  rule: { prefix: string; start: number; digits: number; suffix: string; withSpec: boolean },
  index: number,
  row: Row,
): string {
  const seq = String(rule.start + index).padStart(Math.max(1, rule.digits), "0");
  const head = `${rule.prefix}${seq}${rule.suffix}`;
  if (!rule.withSpec) return head;
  const spec = [row.color, row.size].map((item) => String(item ?? "").trim()).filter(Boolean).join("-");
  return spec ? `${head}-${spec}` : head;
}

export function BatchColumnDialog({ label, kind, decimals = 0, rows, onApply, onClose }: Props) {
  // 文字
  const [text, setText] = useState("");
  // 数值
  const [mode, setMode] = useState<NumberMode>("set");
  const [value, setValue] = useState("");
  const [op, setOp] = useState<Op>("add");
  const [keepDecimals, setKeepDecimals] = useState(decimals > 0);
  // SKU
  const [prefix, setPrefix] = useState("");
  const [start, setStart] = useState("1");
  const [digits, setDigits] = useState("6");
  const [suffix, setSuffix] = useState("");
  const [withSpec, setWithSpec] = useState(false);

  const round = (n: number) => {
    const places = keepDecimals ? decimals : 0;
    const factor = 10 ** places;
    return Math.round(n * factor) / factor;
  };

  const nextValues = useMemo(() => {
    if (kind === "text") return rows.map(() => text.trim());
    if (kind === "sku") {
      const rule = {
        prefix: prefix.trim(),
        start: Math.max(0, Number(start) || 0),
        digits: Math.min(12, Math.max(1, Number(digits) || 1)),
        suffix: suffix.trim(),
        withSpec,
      };
      return rows.map((row, index) => buildSkuCode(rule, index, row));
    }
    const amount = Number(value);
    return rows.map((row) => {
      const base = Number(row.value);
      if (!row.value.trim()) return row.value; // 原本空着的保持空着
      if (!Number.isFinite(amount)) return row.value;
      if (mode === "set") return String(round(amount));
      if (!Number.isFinite(base)) return row.value;
      const next = op === "add" ? base + amount : op === "sub" ? base - amount : base * amount;
      return String(round(Math.max(0, next)));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, rows, text, value, mode, op, keepDecimals, prefix, start, digits, suffix, withSpec]);

  const codes = kind === "sku" ? nextValues : [];
  const dupSku = kind === "sku" && new Set(codes).size !== codes.length;
  const negative = kind === "number" && nextValues.some((item) => Number(item) < 0);
  const emptyText = kind === "text" && !text.trim();
  const blankNumber = kind === "number" && !value.trim();
  const disabled = (kind === "sku" && (!codes.length || dupSku)) || negative || emptyText || blankNumber;

  const hint =
    kind === "text"
      ? "整列改成同一段文字（留空无法提交；想单独改某一行直接点表格里的格子）"
      : kind === "sku"
        ? "按表格顺序递增生成；勾上「添加变种属性」会追加「-颜色-尺码」"
        : "「按现有」是在每行自己的值上算，不是拿第一行算完套给所有行";

  return createPortal(
    <div className="temu-edit-backdrop is-over-batch" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-col-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`批量修改${label}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>批量修改{label}</strong>
            <span>按表格顺序改 {rows.length} 条</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-batch-body">
          <p className="temu-col-dialog-hint">{hint}</p>

          {kind === "text" ? (
            <label className="temu-col-dialog-line">
              <span>改为</span>
              <input
                className="temu-input"
                value={text}
                placeholder="整列统一填这段文字"
                onChange={(event) => setText(event.target.value)}
              />
            </label>
          ) : null}

          {kind === "number" ? (
            <>
              <label className="temu-col-dialog-radio">
                <input type="radio" checked={mode === "set"} onChange={() => setMode("set")} />
                直接修改为
                <input
                  className="temu-input"
                  value={value}
                  placeholder="示例：1.00"
                  onChange={(event) => setValue(event.target.value)}
                />
              </label>
              <label className="temu-col-dialog-radio">
                <input type="radio" checked={mode === "offset"} onChange={() => setMode("offset")} />
                按现有{label}
                <select
                  className="temu-input temu-col-dialog-op"
                  value={op}
                  onChange={(event) => setOp(event.target.value as Op)}
                >
                  <option value="add">加</option>
                  <option value="sub">减</option>
                  <option value="mul">乘</option>
                </select>
                <input
                  className="temu-input"
                  value={value}
                  placeholder="示例：1.00"
                  onChange={(event) => setValue(event.target.value)}
                />
              </label>
              {decimals > 0 ? (
                <label className="temu-col-dialog-check">
                  <input
                    type="checkbox"
                    checked={keepDecimals}
                    onChange={(event) => setKeepDecimals(event.target.checked)}
                  />
                  保留 {decimals} 位小数
                </label>
              ) : null}
            </>
          ) : null}

          {kind === "sku" ? (
            <>
              <div className="temu-col-dialog-grid">
                <label>
                  <span>前缀</span>
                  <input className="temu-input" value={prefix} placeholder="示例：GX" onChange={(e) => setPrefix(e.target.value)} />
                </label>
                <label>
                  <span>起始值</span>
                  <input className="temu-input" value={start} placeholder="示例：1" onChange={(e) => setStart(e.target.value)} />
                </label>
                <label>
                  <span>位数</span>
                  <input className="temu-input" value={digits} placeholder="示例：6" onChange={(e) => setDigits(e.target.value)} />
                </label>
                <label>
                  <span>后缀</span>
                  <input className="temu-input" value={suffix} placeholder="示例：A" onChange={(e) => setSuffix(e.target.value)} />
                </label>
              </div>
              <label className="temu-col-dialog-check">
                <input type="checkbox" checked={withSpec} onChange={(e) => setWithSpec(e.target.checked)} />
                添加变种属性（货号后追加「-颜色-尺码」）
              </label>
            </>
          ) : null}

          <div className="temu-col-dialog-preview">
            <strong>预览</strong>
            {rows.slice(0, 3).map((row, index) => (
              <div key={index} className="temu-col-dialog-diff">
                <span>{row.value || "—"}</span>
                <em>→</em>
                <b>{nextValues[index] || "—"}</b>
              </div>
            ))}
            {rows.length > 3 ? <span className="temu-muted">…共 {rows.length} 条</span> : null}
            {dupSku ? (
              <em className="temu-col-dialog-warn">生成的货号有重复：把位数调大，或勾上「添加变种属性」</em>
            ) : null}
            {negative ? <em className="temu-col-dialog-warn">有值算成了负数，检查一下输入</em> : null}
          </div>
        </div>

        <footer className="temu-edit-foot">
          <button type="button" className="temu-btn" onClick={onClose}>
            取消
          </button>
          <div>
            <button
              type="button"
              className="temu-btn temu-btn-primary"
              disabled={disabled}
              onClick={() => onApply(nextValues)}
            >
              填入 {rows.length} 条
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
