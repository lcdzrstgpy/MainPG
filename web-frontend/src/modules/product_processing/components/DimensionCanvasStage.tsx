import Konva from "konva/lib/Core";
import type { KonvaEventObject } from "konva/lib/Node";
import "konva/lib/shapes/Arrow";
import "konva/lib/shapes/Circle";
import "konva/lib/shapes/Label";
import "konva/lib/shapes/Rect";
import "konva/lib/shapes/Text";
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Arrow, Circle, Group, Label, Layer, Rect, Stage, Tag, Text } from "react-konva/lib/ReactKonvaCore";

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

type PanState = {
  pointerId: number;
  clientX: number;
  clientY: number;
  scrollLeft: number;
  scrollTop: number;
};

type Props = {
  editor: EditorState;
  asset: DimensionAsset | null;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  onSelectAnnotation: (annotationId: string) => void;
  onCommitEditor: (editor: EditorState) => void;
  onCommitAnnotation: (start: NormalizedPoint, end: NormalizedPoint) => void;
};

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.5;
const ZOOM_STEP = 0.15;

function preferredPixelRatio(): number {
  if (typeof window === "undefined") return 1;
  const navigatorWithMemory = navigator as Navigator & { deviceMemory?: number };
  const isLowPower = (navigator.hardwareConcurrency || 4) <= 4
    || (navigatorWithMemory.deviceMemory ?? 4) <= 4;
  return isLowPower ? 1 : Math.min(window.devicePixelRatio || 1, 1.5);
}

Konva.pixelRatio = preferredPixelRatio();

function colorFor(annotation: DimensionAnnotation): string {
  if (annotation.style === "gray_dashed") return "#7b8794";
  return annotation.style === "dark" ? "#111111" : "#ffffff";
}

function lineMetrics(annotation: DimensionAnnotation): { strokeWidth: number; pointerWidth: number } {
  if (annotation.lineWidth === "thin") return { strokeWidth: 2, pointerWidth: 7 };
  if (annotation.lineWidth === "thick") return { strokeWidth: 6, pointerWidth: 13 };
  return { strokeWidth: 3, pointerWidth: 9 };
}

function eventPoint(stage: Konva.Stage, size: number): NormalizedPoint {
  const point = stage.getPointerPosition();
  if (!point) return { x: 0, y: 0 };
  return clampPoint({
    x: point.x / Math.max(1, size),
    y: point.y / Math.max(1, size),
  });
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
  const deltaX = Math.min(
    1 - Math.max(...points.map((item) => item.x)),
    Math.max(-Math.min(...points.map((item) => item.x)), requestedX),
  );
  const deltaY = Math.min(
    1 - Math.max(...points.map((item) => item.y)),
    Math.max(-Math.min(...points.map((item) => item.y)), requestedY),
  );
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

function verticalScrollTarget(from: HTMLElement, deltaY: number): HTMLElement | null {
  let candidate = from.parentElement;
  while (candidate) {
    const style = window.getComputedStyle(candidate);
    const scrollable = /auto|scroll/.test(style.overflowY)
      && candidate.scrollHeight > candidate.clientHeight + 1;
    const canMove = deltaY > 0
      ? candidate.scrollTop < candidate.scrollHeight - candidate.clientHeight - 1
      : candidate.scrollTop > 1;
    if (scrollable && canMove) return candidate;
    candidate = candidate.parentElement;
  }
  return null;
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(value.toFixed(2))));
}

export function DimensionCanvasStage({
  editor,
  asset,
  zoom,
  onZoomChange,
  onSelectAnnotation,
  onCommitEditor,
  onCommitAnnotation,
}: Props) {
  const [drag, setDragState] = useState<DragState | null>(null);
  const [previewEditor, setPreviewEditor] = useState<EditorState | null>(null);
  const [imageState, setImageState] = useState<"loading" | "loaded" | "error" | "empty">("empty");
  const [retryNonce, setRetryNonce] = useState(0);
  const [baseSize, setBaseSize] = useState(560);
  const [spacePressed, setSpacePressed] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const dragRef = useRef<DragState | null>(null);
  const panRef = useRef<PanState | null>(null);
  const frameRef = useRef<number | null>(null);
  const pendingPointRef = useRef<NormalizedPoint | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  const onZoomChangeRef = useRef(onZoomChange);
  const wheelFrameRef = useRef<number | null>(null);
  const pendingWheelYRef = useRef(0);
  const wheelTargetRef = useRef<HTMLElement | null>(null);

  const canvasSize = Math.max(1, Math.round(baseSize * zoom));

  const setDrag = (value: DragState | null) => {
    dragRef.current = value;
    setDragState(value);
  };

  const cancelFrame = () => {
    if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    pendingPointRef.current = null;
  };

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  useEffect(() => {
    onZoomChangeRef.current = onZoomChange;
  }, [onZoomChange]);

  useEffect(() => () => {
    cancelFrame();
    if (wheelFrameRef.current != null) cancelAnimationFrame(wheelFrameRef.current);
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width ?? viewport.clientWidth;
      const height = entry?.contentRect.height ?? viewport.clientHeight;
      const available = Math.min(width - 48, height - 48, 640);
      setBaseSize(Math.max(240, Math.floor(available)));
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;

    const handleWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        const direction = event.deltaY > 0 ? -1 : 1;
        const nextZoom = clampZoom(zoomRef.current + direction * ZOOM_STEP);
        zoomRef.current = nextZoom;
        onZoomChangeRef.current(nextZoom);
        return;
      }
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? 16
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
          ? window.innerHeight
          : 1;
      const deltaY = event.deltaY * unit;
      if (!deltaY) return;
      event.preventDefault();
      pendingWheelYRef.current += deltaY;
      wheelTargetRef.current = verticalScrollTarget(viewport, deltaY);
      if (wheelFrameRef.current != null) return;
      wheelFrameRef.current = requestAnimationFrame(() => {
        wheelFrameRef.current = null;
        const pendingY = pendingWheelYRef.current;
        pendingWheelYRef.current = 0;
        const target = wheelTargetRef.current;
        wheelTargetRef.current = null;
        if (target) target.scrollBy({ top: pendingY, behavior: "auto" });
        else window.scrollBy({ top: pendingY, behavior: "auto" });
      });
    };

    viewport.addEventListener("wheel", handleWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const viewport = viewportRef.current;
      const canvasIsActive = Boolean(viewport && (viewport.matches(":hover") || document.activeElement === viewport));
      if (event.code === "Space" && canvasIsActive && !event.repeat && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) {
        event.preventDefault();
        setSpacePressed(true);
      }
    };
    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.code === "Space") setSpacePressed(false);
    };
    const handleBlur = () => setSpacePressed(false);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    window.addEventListener("blur", handleBlur);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      window.removeEventListener("blur", handleBlur);
    };
  }, []);

  useEffect(() => {
    setImageState(asset?.previewUrl ? "loading" : "empty");
  }, [asset?.previewUrl, retryNonce]);

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
        setPreviewEditor(moveEditor(active, pending));
      }
    });
  };

  const startCreate = (event: KonvaEventObject<PointerEvent>) => {
    const stage = event.target.getStage();
    const isCanvasBackground = event.target === stage || event.target.name() === "canvas-background";
    if (editor.activeTool === "select" || !isCanvasBackground) return;
    event.evt.preventDefault();
    if (!stage) return;
    const point = eventPoint(stage, canvasSize);
    (event.evt.currentTarget as Element | null)?.setPointerCapture?.(event.evt.pointerId);
    setDrag({ kind: "create", start: point, current: point, pointerId: event.evt.pointerId });
  };

  const startMove = (
    event: KonvaEventObject<PointerEvent>,
    annotationId: string,
    part: MovePart,
  ) => {
    if (editor.activeTool !== "select") return;
    event.cancelBubble = true;
    event.evt.preventDefault();
    const stage = event.target.getStage();
    if (!stage) return;
    (event.evt.currentTarget as Element | null)?.setPointerCapture?.(event.evt.pointerId);
    const baseEditor = { ...editor, selectedAnnotationId: annotationId };
    setPreviewEditor(baseEditor);
    setDrag({
      kind: "move",
      annotationId,
      part,
      pointerId: event.evt.pointerId,
      origin: eventPoint(stage, canvasSize),
      baseEditor,
    });
    onSelectAnnotation(annotationId);
  };

  const move = (event: KonvaEventObject<PointerEvent>) => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.evt.pointerId) return;
    event.evt.preventDefault();
    const stage = event.target.getStage();
    if (stage) schedulePreview(eventPoint(stage, canvasSize));
  };

  const finish = (event: KonvaEventObject<PointerEvent>) => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.evt.pointerId) return;
    event.evt.preventDefault();
    const stage = event.target.getStage();
    if (!stage) return;
    const point = eventPoint(stage, canvasSize);
    cancelFrame();
    const eventTarget = event.evt.currentTarget as Element | null;
    if (eventTarget?.hasPointerCapture?.(event.evt.pointerId)) {
      eventTarget.releasePointerCapture(event.evt.pointerId);
    }
    if (active.kind === "create") {
      if (Math.hypot(point.x - active.start.x, point.y - active.start.y) >= 0.01) {
        onCommitAnnotation(active.start, point);
      }
    } else if (Math.hypot(point.x - active.origin.x, point.y - active.origin.y) >= 0.001) {
      onCommitEditor(moveEditor(active, point));
    }
    setPreviewEditor(null);
    setDrag(null);
  };

  const cancel = () => {
    cancelFrame();
    setPreviewEditor(null);
    setDrag(null);
  };

  const startPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const shouldPan = event.button === 1 || (spacePressed && event.button === 0);
    if (!shouldPan) return;
    event.preventDefault();
    event.stopPropagation();
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.setPointerCapture(event.pointerId);
    panRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
    };
    setIsPanning(true);
  };

  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    const viewport = viewportRef.current;
    if (!pan || !viewport || pan.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    viewport.scrollLeft = pan.scrollLeft - (event.clientX - pan.clientX);
    viewport.scrollTop = pan.scrollTop - (event.clientY - pan.clientY);
  };

  const finishPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    const viewport = viewportRef.current;
    if (viewport?.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    panRef.current = null;
    setIsPanning(false);
  };

  const visibleEditor = previewEditor ?? editor;
  const draft = drag?.kind === "create" ? drag : null;

  return (
    <div
      ref={viewportRef}
      className={`dimension-stage-viewport${drag ? " is-dragging" : ""}${spacePressed ? " is-pan-ready" : ""}${isPanning ? " is-panning" : ""}`}
      aria-label="尺寸图编辑画布"
      tabIndex={0}
      onPointerDownCapture={startPan}
      onPointerMoveCapture={movePan}
      onPointerUpCapture={finishPan}
      onPointerCancelCapture={finishPan}
    >
      <div
        className="dimension-stage-surface"
        style={{ width: canvasSize, height: canvasSize }}
      >
        {asset?.previewUrl ? (
          <>
            <img
              key={`${asset.previewUrl}:${retryNonce}`}
              className="dimension-stage-image"
              src={asset.previewUrl}
              alt={asset.role || "尺寸图素材"}
              decoding="async"
              fetchPriority="high"
              draggable={false}
              onDragStart={(event) => event.preventDefault()}
              onLoad={() => setImageState("loaded")}
              onError={() => setImageState("error")}
              style={imageState === "loaded" ? undefined : { display: "none" }}
            />
            {imageState === "loading" && <div className="dimension-stage-status">图片加载中…</div>}
            {imageState === "error" && (
              <div className="dimension-stage-status is-error">
                <span>图片加载失败</span>
                <button
                  type="button"
                  className="dimension-stage-retry"
                  onClick={() => {
                    setImageState("loading");
                    setRetryNonce((value) => value + 1);
                  }}
                >
                  重试
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="dimension-stage-placeholder">请选择可用素材</div>
        )}
        <Stage
          className="dimension-konva-stage"
          width={canvasSize}
          height={canvasSize}
          onPointerDown={startCreate}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={cancel}
        >
          <Layer>
            <Rect name="canvas-background" width={canvasSize} height={canvasSize} fill="rgba(0,0,0,0.001)" />
            {visibleEditor.annotations.map((annotation) => {
              const color = colorFor(annotation);
              const selected = visibleEditor.selectedAnnotationId === annotation.id;
              const metrics = lineMetrics(annotation);
              const points = [
                annotation.start.x * canvasSize,
                annotation.start.y * canvasSize,
                annotation.end.x * canvasSize,
                annotation.end.y * canvasSize,
              ];
              return (
                <Group key={annotation.id}>
                  <Arrow
                    points={points}
                    fill={color}
                    stroke={color}
                    strokeWidth={metrics.strokeWidth + (selected ? 1 : 0)}
                    pointerAtBeginning
                    pointerAtEnding
                    pointerLength={10}
                    pointerWidth={metrics.pointerWidth}
                    dash={annotation.style === "gray_dashed" ? [9, 7] : undefined}
                    shadowColor="#000000"
                    shadowBlur={2}
                    shadowOpacity={0.75}
                    shadowOffsetY={1}
                    perfectDrawEnabled={false}
                    listening={false}
                  />
                  <Arrow
                    points={points}
                    fill="rgba(0,0,0,0.001)"
                    stroke="rgba(0,0,0,0.001)"
                    strokeWidth={24}
                    pointerAtBeginning={false}
                    pointerAtEnding={false}
                    hitStrokeWidth={28}
                    onPointerDown={(event) => startMove(event, annotation.id, "line")}
                  />
                  <Label
                    x={annotation.label.x * canvasSize}
                    y={annotation.label.y * canvasSize}
                    offsetX={36}
                    offsetY={15}
                    onPointerDown={(event) => startMove(event, annotation.id, "label")}
                  >
                    <Tag
                      fill="rgba(8, 27, 48, 0.78)"
                      cornerRadius={6}
                      stroke={selected ? "#24a8e0" : "rgba(255,255,255,.7)"}
                      strokeWidth={selected ? 2 : 1}
                      shadowColor="#000000"
                      shadowBlur={3}
                      shadowOpacity={0.25}
                    />
                    <Text
                      text={formatDimension(annotation.valueCm, annotation.unit)}
                      width={72}
                      height={30}
                      align="center"
                      verticalAlign="middle"
                      fill="#ffffff"
                      fontSize={14}
                      fontStyle="bold"
                      listening={false}
                    />
                  </Label>
                  {selected && (
                    <>
                      <Circle
                        x={annotation.start.x * canvasSize}
                        y={annotation.start.y * canvasSize}
                        radius={7}
                        fill="#ffffff"
                        stroke="#149bd3"
                        strokeWidth={3}
                        hitStrokeWidth={16}
                        onPointerDown={(event) => startMove(event, annotation.id, "start")}
                      />
                      <Circle
                        x={annotation.end.x * canvasSize}
                        y={annotation.end.y * canvasSize}
                        radius={7}
                        fill="#ffffff"
                        stroke="#149bd3"
                        strokeWidth={3}
                        hitStrokeWidth={16}
                        onPointerDown={(event) => startMove(event, annotation.id, "end")}
                      />
                    </>
                  )}
                </Group>
              );
            })}
          </Layer>
          <Layer listening={false}>
            {draft && (
              <Arrow
                points={[
                  draft.start.x * canvasSize,
                  draft.start.y * canvasSize,
                  draft.current.x * canvasSize,
                  draft.current.y * canvasSize,
                ]}
                fill="#149bd3"
                stroke="#149bd3"
                strokeWidth={3}
                dash={[8, 6]}
                pointerAtBeginning
                pointerAtEnding
                pointerLength={10}
                pointerWidth={9}
                perfectDrawEnabled={false}
              />
            )}
          </Layer>
        </Stage>
      </div>
      <div className="dimension-stage-hint" aria-hidden="true">
        普通滚轮滚动页面 · Ctrl + 滚轮缩放 · 空格/中键拖动画布
      </div>
    </div>
  );
}
