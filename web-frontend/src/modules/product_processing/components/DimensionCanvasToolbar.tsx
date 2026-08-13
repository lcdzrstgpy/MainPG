import type { DimensionKey, EditorState } from "../types/dimensionCanvas";

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
  onStyle: (style: "auto" | "dark" | "light") => void;
};

const DIMENSION_TOOLS: Array<{ key: Exclude<DimensionKey, "custom">; label: string }> = [
  { key: "length", label: "长" },
  { key: "width", label: "宽" },
  { key: "height", label: "高" },
];

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
}: Props) {
  const toolReason = (key: Exclude<DimensionKey, "custom">): string => {
    const dimension = editor.dimensions[key];
    if (dimension.valueCm == null) return "缺少商品本体尺寸";
    if (dimension.provenance === "package_estimate") return "物流包裹估算不可用于尺寸图";
    if (dimension.provenance === "unconfirmed") return "请先确认商品本体尺寸";
    return "";
  };
  const customReason = editor.customValueCm && editor.customValueCm > 0 ? "" : "请先填写自定义尺寸";

  return (
    <aside className="dimension-toolbar" aria-label="尺寸画布工具栏">
      <div className="dimension-tool-group">
        <button className={editor.activeTool === "select" ? "is-active" : ""} onClick={() => onTool("select")}>选择 / 移动</button>
        {DIMENSION_TOOLS.map(({ key, label }) => {
          const reason = toolReason(key);
          return (
            <button
              key={key}
              className={editor.activeTool === key ? "is-active" : ""}
              onClick={() => onTool(key)}
              disabled={Boolean(reason)}
              title={reason || `绘制${label}尺寸线`}
            >
              {label}
            </button>
          );
        })}
        <button
          className={editor.activeTool === "custom" ? "is-active" : ""}
          onClick={() => onTool("custom")}
          disabled={Boolean(customReason)}
          title={customReason || "绘制自定义尺寸线"}
        >自定义</button>
      </div>
      <div className="dimension-tool-group is-row">
        <button onClick={onUndo} disabled={!canUndo} title="撤销">↶</button>
        <button onClick={onRedo} disabled={!canRedo} title="重做">↷</button>
        <button onClick={onDelete} disabled={!editor.selectedAnnotationId}>删除</button>
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
      <div className="dimension-tool-group">
        <span className="dimension-tool-label">标注样式</span>
        <div className="dimension-tool-row">
          <button onClick={() => onStyle("auto")}>自动</button>
          <button onClick={() => onStyle("dark")}>黑</button>
          <button onClick={() => onStyle("light")}>白</button>
        </div>
      </div>
    </aside>
  );
}
