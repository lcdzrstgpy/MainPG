import type { DimensionKey, DimensionLineWidth, EditorState } from "../types/dimensionCanvas";
import { centimetersToUnit, unitToCentimeters } from "../data/dimensionCanvasModel";

type Props = {
  editor: EditorState;
  canUndo: boolean;
  canRedo: boolean;
  onTool: (tool: DimensionKey | "select") => void;
  onUndo: () => void;
  onRedo: () => void;
  onDelete: () => void;
  onFit: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  onStyle: (style: "auto" | "dark" | "light" | "gray_dashed") => void;
  onLineWidth: (lineWidth: DimensionLineWidth) => void;
  onCustomValueChange: (valueCm: number | null) => void;
};

const DIMENSION_TOOLS: Array<{ key: Exclude<DimensionKey, "custom">; label: string }> = [
  { key: "length", label: "长" },
  { key: "width", label: "宽" },
  { key: "height", label: "高" },
];

const ANNOTATION_STYLES = [
  { key: "auto", label: "自动", title: "自动颜色" },
  { key: "dark", label: "黑", title: "黑色实线" },
  { key: "light", label: "白", title: "白色实线" },
  { key: "gray_dashed", label: "灰虚线", title: "灰色虚线" },
] as const;

export function DimensionCanvasToolbar({
  editor,
  canUndo,
  canRedo,
  onTool,
  onUndo,
  onRedo,
  onDelete,
  onFit,
  onZoomIn,
  onZoomOut,
  onReset,
  onStyle,
  onLineWidth,
  onCustomValueChange,
}: Props) {
  const selectedAnnotation = editor.annotations.find((annotation) => annotation.id === editor.selectedAnnotationId);
  const toolReason = (key: Exclude<DimensionKey, "custom">): string => {
    const dimension = editor.dimensions[key];
    if (dimension.valueCm == null || dimension.valueCm <= 0) return "缺少商品本体尺寸";
    return "";
  };
  const toolHint = (key: Exclude<DimensionKey, "custom">, label: string): string => {
    const provenance = editor.dimensions[key].provenance;
    return provenance === "package_estimate" || provenance === "unconfirmed"
      ? `点击确认${label}的数值并进入绘制`
      : `绘制${label}尺寸线`;
  };
  const customReason = editor.customValueCm && editor.customValueCm > 0 ? "" : "请先填写自定义尺寸";

  return (
    <aside className="dimension-toolbar" aria-label="尺寸画布工具栏">
      <div className="dimension-tool-group dimension-tool-main">
        <span className="dimension-tool-label">绘制工具</span>
        <button className={`dimension-tool-primary${editor.activeTool === "select" ? " is-active" : ""}`} onClick={() => onTool("select")}>
          <span aria-hidden="true">⌖</span>选择 / 移动
        </button>
        {DIMENSION_TOOLS.map(({ key, label }) => {
          const reason = toolReason(key);
          return (
            <button
              key={key}
              className={`dimension-tool-primary${editor.activeTool === key ? " is-active" : ""}`}
              onClick={() => onTool(key)}
              disabled={Boolean(reason)}
              title={reason || toolHint(key, label)}
              aria-pressed={editor.activeTool === key}
            >
              <span className="dimension-tool-axis" aria-hidden="true" />{label}
            </button>
          );
        })}
        <div className="dimension-custom-tool">
          <button
            className={`dimension-tool-primary${editor.activeTool === "custom" ? " is-active" : ""}`}
            onClick={() => onTool("custom")}
            disabled={Boolean(customReason)}
            title={customReason || "绘制自定义尺寸线"}
          ><span aria-hidden="true">＋</span>自定义</button>
          <label className="dimension-custom-tool-value">
            <span>尺寸</span>
            <input
              type="number"
              min="0"
              step={editor.displayUnit === "mm" ? "1" : "0.01"}
              placeholder="输入数值"
              aria-label="自定义尺寸数值"
              value={editor.customValueCm == null ? "" : Number(centimetersToUnit(editor.customValueCm, editor.displayUnit).toFixed(editor.displayUnit === "mm" ? 1 : 2))}
              onChange={(event) => {
                const parsed = Number(event.target.value);
                onCustomValueChange(
                  event.target.value === "" || !Number.isFinite(parsed) || parsed <= 0
                    ? null
                    : unitToCentimeters(parsed, editor.displayUnit),
                );
              }}
            />
            <em>{editor.displayUnit}</em>
          </label>
        </div>
      </div>
      <div className="dimension-tool-group dimension-history-tools is-row" aria-label="编辑操作">
        <button onClick={onUndo} disabled={!canUndo} title="撤销" aria-label="撤销">↶</button>
        <button onClick={onRedo} disabled={!canRedo} title="重做" aria-label="重做">↷</button>
        <button onClick={onDelete} disabled={!editor.selectedAnnotationId} title="删除选中的尺寸线">删除</button>
      </div>
      <div className="dimension-tool-group">
        <span className="dimension-tool-label">视图</span>
        <div className="dimension-tool-row">
          <button onClick={onFit}>适应</button>
          <button onClick={onZoomOut} aria-label="缩小">−</button>
          <button onClick={onZoomIn} aria-label="放大">＋</button>
        </div>
        <button onClick={onReset}>重置视图</button>
      </div>
      <div className="dimension-tool-group dimension-appearance-tools">
        <div className="dimension-tool-label-row">
          <span className="dimension-tool-label">标注样式</span>
          {!selectedAnnotation && <small>先选尺寸线</small>}
        </div>
        <div className="dimension-tool-row dimension-choice-row dimension-style-row">
          {ANNOTATION_STYLES.map(({ key: style, label, title }) => (
            <button
              key={style}
              className={selectedAnnotation?.style === style ? "is-active" : ""}
              disabled={!selectedAnnotation}
              onClick={() => onStyle(style)}
              aria-pressed={selectedAnnotation?.style === style}
              title={selectedAnnotation ? `设置为${title}` : "请先选择一条尺寸线"}
            >
              <span className={`dimension-color-dot is-${style}`} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
        <span className="dimension-tool-label dimension-tool-sublabel">线条粗细</span>
        <div className="dimension-tool-row dimension-choice-row dimension-width-row">
          {(["thin", "normal", "thick"] as const).map((lineWidth, index) => (
            <button
              key={lineWidth}
              className={selectedAnnotation?.lineWidth === lineWidth ? "is-active" : ""}
              disabled={!selectedAnnotation}
              onClick={() => onLineWidth(lineWidth)}
              title={selectedAnnotation ? `设置为${["细", "标准", "粗"][index]}线` : "请先选择一条尺寸线"}
              aria-pressed={selectedAnnotation?.lineWidth === lineWidth}
            >
              <span className={`dimension-width-swatch is-${lineWidth}`} aria-hidden="true" />
              {(["细", "标准", "粗"] as const)[index]}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
