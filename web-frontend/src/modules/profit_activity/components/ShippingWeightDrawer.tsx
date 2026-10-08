import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent as ReactDragEvent } from "react";
import { createPortal } from "react-dom";

import { extractShippingMetrics } from "../api/profitActivityApi";

/** 抛重口径：长(cm)×宽(cm)×高(cm)÷7000。 */
const VOLUMETRIC_DIVISOR = 7000;

type Props = {
  /** 表单里当前的「重量 KG」，打开侧边栏时作为实际重量初值。 */
  initialWeightKg: string;
  /** 勾选「按实际重量计算（不计算抛重）」，由页面持有以便跨开关保留。 */
  useActualOnly: boolean;
  onUseActualOnlyChange: (value: boolean) => void;
  /** 应用到「重量 KG」字段。 */
  onApply: (weightKg: string) => void;
  onClose: () => void;
};

function parseNumber(value: string): number | null {
  const parsed = Number(String(value).replace(/[,\s]/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function formatWeight(value: number): string {
  return String(Number(value.toFixed(4)));
}

/**
 * 「重量与尺寸」侧边栏：粘贴/上传物流或包装截图，本地算法（后端 RapidOCR）提取
 * 实际重量与长宽高，可手动修改；展示抛重与计费重量，并把最终重量回填到重量字段。
 */
export function ShippingWeightDrawer({
  initialWeightKg,
  useActualOnly,
  onUseActualOnlyChange,
  onApply,
  onClose,
}: Props) {
  const [image, setImage] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [actual, setActual] = useState(initialWeightKg);
  const [length, setLength] = useState("");
  const [width, setWidth] = useState("");
  const [height, setHeight] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [linesOpen, setLinesOpen] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!image) {
      setPreviewUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(image);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const runExtract = useCallback(async (file: File) => {
    setImage(file);
    setError("");
    setBusy(true);
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    try {
      const result = await extractShippingMetrics(file);
      if (requestId !== requestRef.current) return;
      const metrics = result.metrics;
      if (metrics.actual_weight_kg != null) setActual(String(metrics.actual_weight_kg));
      if (metrics.length_cm != null) setLength(String(metrics.length_cm));
      if (metrics.width_cm != null) setWidth(String(metrics.width_cm));
      if (metrics.height_cm != null) setHeight(String(metrics.height_cm));
      setLines(result.lines ?? []);
      if (metrics.actual_weight_kg == null && metrics.length_cm == null) {
        setError("未从图片中识别到重量或尺寸，请手动填写。");
      }
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(err instanceof Error ? err.message : "识别失败，请重试或手动填写。");
    } finally {
      if (requestId === requestRef.current) setBusy(false);
    }
  }, []);

  // 侧边栏打开期间，Ctrl+V 粘贴图片即可识别（无需先点选图片区）。
  useEffect(() => {
    const handler = (event: ClipboardEvent) => {
      const file = [...(event.clipboardData?.files ?? [])].find((item) => item.type.startsWith("image/"));
      if (!file) return;
      event.preventDefault();
      void runExtract(file);
    };
    document.addEventListener("paste", handler);
    return () => document.removeEventListener("paste", handler);
  }, [runExtract]);

  const actualValue = parseNumber(actual);
  const dims = [parseNumber(length), parseNumber(width), parseNumber(height)];
  const volumetric =
    dims.every((value): value is number => value != null && value > 0)
      ? (dims[0] * dims[1] * dims[2]) / VOLUMETRIC_DIVISOR
      : null;
  const billable =
    actualValue == null
      ? volumetric
      : useActualOnly || volumetric == null
        ? actualValue
        : Math.max(actualValue, volumetric);

  const onDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const file = [...event.dataTransfer.files].find((item) => item.type.startsWith("image/"));
    if (file) void runExtract(file);
  };

  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void runExtract(file);
    event.target.value = "";
  };

  const clearImage = () => {
    requestRef.current += 1;
    setImage(null);
    setLines([]);
    setError("");
    setBusy(false);
  };

  const handleApply = () => {
    if (billable == null || billable <= 0) {
      setError("请先识别或填写实际重量/长宽高。");
      return;
    }
    onApply(formatWeight(billable));
    onClose();
  };

  return createPortal(
    <div className="profit-source-drawer-root">
      <div className="profit-source-drawer-mask" onClick={onClose} />
      <aside className="profit-source-drawer profit-shipping-drawer">
        <header className="profit-source-drawer-head">
          <div>
            <p className="eyebrow">重量与尺寸</p>
            <h2>从截图提取重量</h2>
            <p>上传或 Ctrl+V 粘贴物流/包装截图，本地算法自动识别重量与长宽高，可手动修改。</p>
          </div>
          <div className="profit-source-drawer-head-actions">
            <button className="profit-source-drawer-close" onClick={onClose} aria-label="关闭">×</button>
          </div>
        </header>
        <div className="profit-source-drawer-body">
          <div className="profit-shipping-drop" tabIndex={0} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
            {previewUrl ? (
              <img className="profit-shipping-preview" src={previewUrl} alt="重量截图预览" />
            ) : (
              <div className="profit-shipping-drop-empty">粘贴 / 拖入 / 选择重量截图</div>
            )}
            <div className="profit-shipping-drop-actions">
              <label className="profit-file-button">选择图片<input type="file" accept="image/*" onChange={onChange} /></label>
              {image ? <button type="button" onClick={clearImage}>移除</button> : null}
            </div>
          </div>

          {busy ? <p className="profit-shipping-status">正在识别…</p> : null}
          {error ? <p className="profit-shipping-status is-error">{error}</p> : null}

          <div className="profit-shipping-grid">
            <label>实际重量 KG<input value={actual} onChange={(event) => setActual(event.target.value)} placeholder="如 1.2" /></label>
            <label>长 cm<input value={length} onChange={(event) => setLength(event.target.value)} placeholder="如 30" /></label>
            <label>宽 cm<input value={width} onChange={(event) => setWidth(event.target.value)} placeholder="如 20" /></label>
            <label>高 cm<input value={height} onChange={(event) => setHeight(event.target.value)} placeholder="如 10" /></label>
          </div>

          <div className="profit-shipping-result">
            <div><span>抛重（长×宽×高÷{VOLUMETRIC_DIVISOR}）</span><strong>{volumetric == null ? "—" : `${formatWeight(volumetric)} KG`}</strong></div>
            <div><span>计费重量</span><strong>{billable == null ? "—" : `${formatWeight(billable)} KG`}</strong></div>
          </div>

          <section className="profit-shipping-options">
            <button type="button" className="profit-shipping-options-toggle" onClick={() => setOptionsOpen((value) => !value)} aria-expanded={optionsOpen}>
              <span>计费口径</span>
              <span aria-hidden="true">{optionsOpen ? "▾" : "▸"}</span>
            </button>
            {optionsOpen ? (
              <label className="profit-shipping-checkbox">
                <input type="checkbox" checked={useActualOnly} onChange={(event) => onUseActualOnlyChange(event.target.checked)} />
                按实际重量计算（不计算抛重）
              </label>
            ) : null}
          </section>

          {lines.length > 0 ? (
            <section className="profit-shipping-options">
              <button type="button" className="profit-shipping-options-toggle" onClick={() => setLinesOpen((value) => !value)} aria-expanded={linesOpen}>
                <span>识别文本 · {lines.length} 行</span>
                <span aria-hidden="true">{linesOpen ? "▾" : "▸"}</span>
              </button>
              {linesOpen ? (
                <ul className="profit-shipping-lines">
                  {lines.map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}
                </ul>
              ) : null}
            </section>
          ) : null}

          <div className="profit-shipping-actions">
            <button type="button" className="primary-button" onClick={handleApply} disabled={busy || billable == null}>应用到重量 KG</button>
            <button type="button" onClick={onClose}>取消</button>
          </div>
        </div>
      </aside>
    </div>,
    document.body,
  );
}
