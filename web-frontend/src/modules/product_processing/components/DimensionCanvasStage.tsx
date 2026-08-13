import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { clampPoint, formatDimension, updateAnnotation } from "../data/dimensionCanvasModel";
import type {
  DimensionAnnotation,
  DimensionAsset,
  EditorState,
  NormalizedPoint,
} from "../types/dimensionCanvas";

type MovePart = "start" | "end" | "label" | "line";
type DragState =
  | { kind: "create"; start: NormalizedPoint; current: NormalizedPoint; pointerId: number }
  | {
      kind: "move";
      annotationId: string;
      part: MovePart;
      pointerId: number;
      origin: NormalizedPoint;
      baseEditor: EditorState;
    };

type Props = {
  editor: EditorState;
  asset: DimensionAsset | null;
  zoom: number;
  onSelectAnnotation: (annotationId: string) => void;
  onCommitEditor: (editor: EditorState) => void;
  onCommitAnnotation: (start: NormalizedPoint, end: NormalizedPoint) => void;
};

function eventPoint(
  svg: SVGSVGElement,
  event: Pick<ReactPointerEvent<SVGElement>, "clientX" | "clientY">,
): NormalizedPoint {
  const rect = svg.getBoundingClientRect();
  return clampPoint({
    x: (event.clientX - rect.left) / Math.max(1, rect.width),
    y: (event.clientY - rect.top) / Math.max(1, rect.height),
  });
}

function colorFor(annotation: DimensionAnnotation): string {
  return annotation.style === "dark" ? "#111111" : "#ffffff";
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

function translateAnnotation(
  editor: EditorState,
  annotationId: string,
  origin: NormalizedPoint,
  point: NormalizedPoint,
): EditorState {
  const annotation = editor.annotations.find((item) => item.id === annotationId);
  if (!annotation) return editor;
  const points = [annotation.start, annotation.end, annotation.label];
  const requestedX = point.x - origin.x;
  const requestedY = point.y - origin.y;
  const deltaX = Math.min(1 - Math.max(...points.map((item) => item.x)), Math.max(-Math.min(...points.map((item) => item.x)), requestedX));
  const deltaY = Math.min(1 - Math.max(...points.map((item) => item.y)), Math.max(-Math.min(...points.map((item) => item.y)), requestedY));
  const move = (value: NormalizedPoint) => ({ x: value.x + deltaX, y: value.y + deltaY });
  return updateAnnotation(editor, annotationId, {
    start: move(annotation.start),
    end: move(annotation.end),
    label: move(annotation.label),
  });
}

function moveEditor(drag: Extract<DragState, { kind: "move" }>, point: NormalizedPoint): EditorState {
  if (drag.part === "line") {
    return translateAnnotation(drag.baseEditor, drag.annotationId, drag.origin, point);
  }
  return updateAnnotation(drag.baseEditor, drag.annotationId, { [drag.part]: point });
}

export function DimensionCanvasStage({
  editor,
  asset,
  zoom,
  onSelectAnnotation,
  onCommitEditor,
  onCommitAnnotation,
}: Props) {
  const [drag, setDragState] = useState<DragState | null>(null);
  const [previewEditor, setPreviewEditor] = useState<EditorState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const frameRef = useRef<number | null>(null);
  const pendingPointRef = useRef<NormalizedPoint | null>(null);

  const setDrag = (value: DragState | null) => {
    dragRef.current = value;
    setDragState(value);
  };

  const setPreview = (value: EditorState | null) => {
    setPreviewEditor(value);
  };

  const cancelFrame = () => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    pendingPointRef.current = null;
  };

  useEffect(() => () => cancelFrame(), []);

  const schedulePreview = (point: NormalizedPoint) => {
    pendingPointRef.current = point;
    if (frameRef.current != null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const active = dragRef.current;
      const pending = pendingPointRef.current;
      pendingPointRef.current = null;
      if (!active || !pending) return;
      if (active.kind === "create") {
        setDrag({ ...active, current: pending });
      } else {
        setPreview(moveEditor(active, pending));
      }
    });
  };

  const startCreate = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (editor.activeTool === "select" || event.target !== event.currentTarget) return;
    event.preventDefault();
    const point = eventPoint(event.currentTarget, event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ kind: "create", start: point, current: point, pointerId: event.pointerId });
  };

  const startMove = (
    event: ReactPointerEvent<SVGElement>,
    annotationId: string,
    part: MovePart,
  ) => {
    if (editor.activeTool !== "select") return;
    event.preventDefault();
    event.stopPropagation();
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    svg.setPointerCapture(event.pointerId);
    const baseEditor = { ...editor, selectedAnnotationId: annotationId };
    setPreview(baseEditor);
    setDrag({
      kind: "move",
      annotationId,
      part,
      pointerId: event.pointerId,
      origin: eventPoint(svg, event),
      baseEditor,
    });
    onSelectAnnotation(annotationId);
  };

  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    schedulePreview(eventPoint(event.currentTarget, event));
  };

  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    const point = eventPoint(event.currentTarget, event);
    cancelFrame();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (active.kind === "create") {
      if (Math.hypot(point.x - active.start.x, point.y - active.start.y) >= 0.01) {
        onCommitAnnotation(active.start, point);
      }
    } else {
      if (Math.hypot(point.x - active.origin.x, point.y - active.origin.y) >= 0.001) {
        onCommitEditor(moveEditor(active, point));
      }
    }
    setPreview(null);
    setDrag(null);
  };

  const cancel = () => {
    cancelFrame();
    setPreview(null);
    setDrag(null);
  };

  const visibleEditor = previewEditor ?? editor;
  const draft = drag?.kind === "create" ? drag : null;

  return (
    <div className={`dimension-stage-viewport${drag ? " is-dragging" : ""}`} aria-label="尺寸图编辑画布">
      <div className="dimension-stage-surface" style={{ transform: `scale(${zoom})` }}>
        {asset?.previewUrl ? (
          <img
            className="dimension-stage-image"
            src={asset.previewUrl}
            alt={asset.role || "尺寸图素材"}
            draggable={false}
            onDragStart={(event) => event.preventDefault()}
          />
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
          onPointerCancel={cancel}
          onLostPointerCapture={() => {
            if (dragRef.current) cancel();
          }}
          onDragStart={(event) => event.preventDefault()}
        >
          {visibleEditor.annotations.map((annotation) => {
            const color = colorFor(annotation);
            const selected = visibleEditor.selectedAnnotationId === annotation.id;
            return (
              <g key={annotation.id} className={`dimension-annotation${selected ? " is-selected" : ""}`}>
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
                <line
                  className="dimension-line-hit"
                  x1={annotation.start.x * 1000}
                  y1={annotation.start.y * 1000}
                  x2={annotation.end.x * 1000}
                  y2={annotation.end.y * 1000}
                  onPointerDown={(event) => startMove(event, annotation.id, "line")}
                />
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
                  {formatDimension(annotation.valueCm, annotation.unit)}
                </text>
                <circle
                  cx={annotation.start.x * 1000}
                  cy={annotation.start.y * 1000}
                  r="24"
                  className="dimension-endpoint-hit"
                  onPointerDown={(event) => startMove(event, annotation.id, "start")}
                />
                <circle
                  cx={annotation.end.x * 1000}
                  cy={annotation.end.y * 1000}
                  r="24"
                  className="dimension-endpoint-hit"
                  onPointerDown={(event) => startMove(event, annotation.id, "end")}
                />
              </g>
            );
          })}
          {draft && (
            <g className="dimension-draft-line">
              <line
                x1={draft.start.x * 1000}
                y1={draft.start.y * 1000}
                x2={draft.current.x * 1000}
                y2={draft.current.y * 1000}
                vectorEffect="non-scaling-stroke"
              />
              <polygon points={arrowPoints(draft.start, draft.current)} />
              <polygon points={arrowPoints(draft.current, draft.start)} />
            </g>
          )}
        </svg>
      </div>
    </div>
  );
}
