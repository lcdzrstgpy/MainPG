import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

/**
 * 两个「按规则批量改」的弹窗（对齐店小秘）：
 *   1. SKU 高级生成规则：前缀 + 起始值 + 位数 + 后缀（可选追加变种属性）
 *   2. 批量修改申报价格：直接改为 / 在现有价上加·减·乘 / 保留两位小数
 *
 * 为什么不能只用「列头填一个值」：货号要按行递增、价格要按现有价换算，
 * 一个固定值做不到，硬填就会出现一堆一样的货号（Temu 直接判 ext_code 重复）。
 */

type SkuRuleProps = {
  /** 用于预览的变种信息（按表格行顺序） */
  rows: Array<{ color?: string; size?: string }>;
  onApply: (codes: string[]) => void;
  onClose: () => void;
};

/** 生成一条货号：前缀 + 序号(补零) + 后缀 [+ -颜色-尺码] */
export function buildSkuCode(
  rule: { prefix: string; start: number; digits: number; suffix: string; withSpec: boolean },
  index: number,
  row: { color?: string; size?: string },
): string {
  const seq = String(rule.start + index).padStart(Math.max(1, rule.digits), "0");
  const head = `${rule.prefix}${seq}${rule.suffix}`;
  if (!rule.withSpec) return head;
  const spec = [row.color, row.size].map((item) => String(item ?? "").trim()).filter(Boolean).join("-");
  return spec ? `${head}-${spec}` : head;
}

export function BatchSkuRuleDialog({ rows, onApply, onClose }: SkuRuleProps) {
  const [prefix, setPrefix] = useState("");
  const [start, setStart] = useState("1");
  const [digits, setDigits] = useState("6");
  const [suffix, setSuffix] = useState("");
  const [withSpec, setWithSpec] = useState(false);

  const rule = {
    prefix: prefix.trim(),
    start: Math.max(0, Number(start) || 0),
    digits: Math.min(12, Math.max(1, Number(digits) || 1)),
    suffix: suffix.trim(),
    withSpec,
  };
  const codes = useMemo(
    () => rows.map((row, index) => buildSkuCode(rule, index, row)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, rule.prefix, rule.start, rule.digits, rule.suffix, rule.withSpec],
  );
  const dup = new Set(codes).size !== codes.length;

  return createPortal(
    <div className="temu-edit-backdrop is-over-batch" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-sku-rule"
        role="dialog"
        aria-modal="true"
        aria-label="SKU 高级生成规则"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>SKU 高级生成规则</strong>
            <span>按表格顺序生成 {rows.length} 条货号</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-batch-body">
          <div className="temu-rule-grid">
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

          <label className="temu-rule-check">
            <input type="checkbox" checked={withSpec} onChange={(e) => setWithSpec(e.target.checked)} />
            添加变种属性（货号后追加「-颜色-尺码」）
          </label>

          <div className="temu-rule-preview">
            <strong>预览</strong>
            {codes.slice(0, 3).map((code, index) => (
              <code key={index}>{code || "（空）"}</code>
            ))}
            {codes.length > 3 ? <span className="temu-muted">…共 {codes.length} 条</span> : null}
            {dup ? <em className="temu-rule-warn">生成的货号有重复：把位数调大，或勾上「添加变种属性」</em> : null}
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
              disabled={!codes.length || dup}
              onClick={() => onApply(codes)}
            >
              生成并填入
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

type PriceProps = {
  /** 每行当前的申报价（元，可能为空串） */
  current: string[];
  onApply: (prices: string[]) => void;
  onClose: () => void;
};

type PriceMode = "set" | "offset";

export function BatchPriceDialog({ current, onApply, onClose }: PriceProps) {
  const [mode, setMode] = useState<PriceMode>("set");
  const [value, setValue] = useState("");
  const [op, setOp] = useState<"add" | "sub" | "mul">("add");
  const [keepTwo, setKeepTwo] = useState(true);

  const numbers = useMemo(() => current.map((item) => Number(item)), [current]);
  const round = (n: number) => (keepTwo ? Math.round(n * 100) / 100 : n);
  const prices = useMemo(() => {
    const amount = Number(value);
    return current.map((raw) => {
      const base = Number(raw);
      if (!raw.trim()) return raw; // 原本空着的保持空着
      if (mode === "set") {
        if (!Number.isFinite(amount)) return raw;
        return String(round(amount));
      }
      if (!Number.isFinite(amount) || !Number.isFinite(base)) return raw;
      const next = op === "add" ? base + amount : op === "sub" ? base - amount : base * amount;
      return String(round(Math.max(0, next)));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, mode, value, op, keepTwo]);
  const invalid = prices.some((item) => Number(item) < 0 || Number.isNaN(Number(item)) && item !== "");

  return createPortal(
    <div className="temu-edit-backdrop is-over-batch" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-price-rule"
        role="dialog"
        aria-modal="true"
        aria-label="批量修改申报价格"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>批量修改申报价格</strong>
            <span>按表格顺序改 {current.length} 条</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-batch-body">
          <label className="temu-rule-radio">
            <input type="radio" checked={mode === "set"} onChange={() => setMode("set")} />
            直接修改为
            <input
              className="temu-input"
              value={value}
              placeholder="示例：1.00"
              onChange={(event) => setValue(event.target.value)}
            />
          </label>

          <label className="temu-rule-radio">
            <input type="radio" checked={mode === "offset"} onChange={() => setMode("offset")} />
            按现有申报价格
            <select
              className="temu-input temu-rule-op"
              value={op}
              onChange={(event) => setOp(event.target.value as typeof op)}
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

          <label className="temu-rule-check">
            <input type="checkbox" checked={keepTwo} onChange={(e) => setKeepTwo(e.target.checked)} />
            保留 2 位小数
          </label>

          <div className="temu-rule-preview">
            <strong>预览</strong>
            {current.slice(0, 3).map((before, index) => (
              <span key={index} className="temu-rule-diff">
                {before || "—"} <em>→</em> {prices[index] || "—"}
              </span>
            ))}
            {current.length > 3 ? <span className="temu-muted">…共 {current.length} 条</span> : null}
            {invalid ? <em className="temu-rule-warn">有价格算成了负数或非数字，检查一下输入</em> : null}
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
              disabled={!value.trim() || invalid || numbers.every((n) => !Number.isFinite(n))}
              onClick={() => onApply(prices)}
            >
              修改并填入
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
