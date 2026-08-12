import { useState, type PointerEvent as ReactPointerEvent } from "react";

import { clampPoint, formatCentimeters, updateAnnotation } from "../data/dimensionCanvasModel";
import type {
  DimensionAnnotation,
  DimensionAsset,
  EditorState,
  NormalizedPoint,
} from "../types/dimensionCanvas";

type DragState =
  | { kind: "create"; start: NormalizedPoint; current: NormalizedPoint; pointerId: number }
  | {
      kind: "move";
      annotationId: string;
      part: "start" | "end" | "label";
      pointerId: number;
    };

type Props = {
  editor: EditorState;
  asset: DimensionAsset | null;
  zoom: number;
  onChange: (editor: EditorState) => void;
  onCommitAnnotation: (start: NormalizedPoint, end: NormalizedPoint) => void;
};

function eventPoint(event: ReactPointerEvent<SVGSVGElement>): NormalizedPoint {
  const rect = event.currentTarget.getBoundingClientRect();
  return clampPoint({
    x: (event.clientX - rect.left) / rect.width,
    y: (event.clientY - rect.top) / rect.height,
  });
}

function colorFor(annotation: DimensionAnnotation): string {
  if (annotation.style === "dark") return "#111111";
  return "#ffffff";
}

function arrowPoints(tip: NormalizedPoint, other: NormalizedPoint): string {
  const tx = tip.x * 1000;
  const ty = tip.y * 1000;
  const ox = other.x * 1000;
  const oy = other.y * 1000;
  const distance = Math.hypot(ox - tx, oy - ty) || 1;
  const ux = (ox - tx) / distance;
  const uy = (oy - ty) / distance;
  const bx = tx + ux * 18;
  const by = ty + uy * 18;
  const px = -uy * 8;
  const py = ux * 8;
  return `${tx},${ty} ${bx + px},${by + py} ${bx - px},${by - py}`;
}

export function DimensionCanvasStage({ editor, asset, zoom, onChange, onCommitAnnotation }: Props) {
  const [drag, setDrag] = useState<DragState | null>(null);

  const startCreate = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (editor.activeTool === "select" || event.target !== event.currentTarget) return;
    const point = eventPoint(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ kind: "create", start: point, current: point, pointerId: event.pointerId });
  };

  const startMove = (
    event: ReactPointerEvent<SVGElement>,
    annotationId: string,
    part: "start" | "end" | "label",
  ) => {
    if (editor.activeTool !== "select") return;
    event.stopPropagation();
    const svg = event.currentTarget.ownerSVGElement;
    svg?.setPointerCapture(event.pointerId);
    setDrag({ kind: "move", annotationId, part, pointerId: event.pointerId });
    onChange({ ...editor, selectedAnnotationId: annotationId });
  };

  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = eventPoint(event);
    if (drag.kind === "create") {
      setDrag({ ...drag, current: point });
      return;
    }
    onChange(updateAnnotation(editor, drag.annotationId, { [drag.part]: point }));
  };

  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (drag.kind === "create") {
      const end = eventPoint(event);
      if (Math.hypot(end.x - drag.start.x, end.y - drag.start.y) >= 0.01) {
        onCommitAnnotation(drag.start, end);
      }
    }
    setDrag(null);
  };

  const preview = drag?.kind === "create" ? drag : null;

  return (
    <div className="dimension-stage-viewport" aria-label="尺寸图编辑画布">
      <div className="dimension-stage-surface" style={{ transform: `scale(${zoom})` }}>
        {asset?.previewUrl ? (
          <img className="dimension-stage-image" src={asset.previewUrl} alt={asset.role || "尺寸图素材"} />
        ) : (
          <div className="dimension-stage-placeholder">请选择可用素材</div>
        )}
        <svg
          className="dimension-stage-svg"
          viewBox="0 0 1000 1000"
          role="img"
          aria-label="拖动两点绘制双箭头尺寸线"
          onPointerDown={startCreate}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={() => setDrag(null)}
        >
          {editor.annotations.map((annotation) => {
            const color = colorFor(annotation);
            const selected = editor.selectedAnnotationId === annotation.id;
            return (
              <g
                key={annotation.id}
                className={`dimension-annotation${selected ? " is-selected" : ""}`}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  onChange({ ...editor, selectedAnnotationId: annotation.id });
                }}
              >
                <line
                  x1={annotation.start.x * 1000}
                  y1={annotation.start.y * 1000}
                  x2={annotation.end.x * 1000}
                  y2={annotation.end.y * 1000}
                  stroke={color}
                  strokeWidth={selected ? 5 : 4}
                  vectorEffect="non-scaling-stroke"
                />
                <polygon points={arrowPoints(annotation.start, annotation.end)} fill={color} />
                <polygon points={arrowPoints(annotation.end, annotation.start)} fill={color} />
                <text
                  x={annotation.label.x * 1000}
                  y={annotation.label.y * 1000}
                  fill={color}
                  stroke={annotation.style === "light" ? "#111111" : "#000000"}
                  strokeWidth="3"
                  paintOrder="stroke"
                  textAnchor="middle"
                  className="dimension-annotation-label"
                  onPointerDown={(event) => startMove(event, annotation.id, "label")}
                >
                  {formatCentimeters(annotation.valueCm)}
                </text>
                <circle
                  cx={annotation.start.x * 1000}
                  cy={annotation.start.y * 1000}
                  r="22"
                  className="dimension-endpoint-hit"
                  onPointerDown={(event) => startMove(event, annotation.id, "start")}
                />
                <circle
                  cx={annotation.end.x * 1000}
                  cy={annotation.end.y * 1000}
                  r="22"
                  className="dimension-endpoint-hit"
                  onPointerDown={(event) => startMove(event, annotation.id, "end")}
                />
              </g>
            );
          })}
          {preview && (
            <line
              className="dimension-draft-line"
              x1={preview.start.x * 1000}
              y1={preview.start.y * 1000}
              x2={preview.current.x * 1000}
              y2={preview.current.y * 1000}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      </div>
    </div>
  );
}
