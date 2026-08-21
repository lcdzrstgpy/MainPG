import type { KonvaEventObject } from "konva/lib/Node";
import "konva/lib/shapes/Circle";
import "konva/lib/shapes/Line";
import "konva/lib/shapes/Rect";
import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Layer, Line, Rect, Stage } from "react-konva/lib/ReactKonvaCore";

import { clampTemplateCalibration } from "../data/podCustomizationModel";
import { usePodAssetUrl } from "../data/usePodAssetUrl";
import type { PodTemplate, PodTemplateCalibration, PodTemplateMask } from "../types";

type Props = {
  template: PodTemplate;
  calibration: PodTemplateCalibration;
  disabled?: boolean;
  onChange: (calibration: PodTemplateCalibration) => void;
};

type Corner = "north-west" | "north-east" | "south-east" | "south-west";

const HANDLE_RADIUS = 7;

function cornerPoint(mask: PodTemplateMask, corner: Corner) {
  return {
    x: corner.endsWith("east") ? mask.x + mask.width : mask.x,
    y: corner.startsWith("south") ? mask.y + mask.height : mask.y,
  };
}

function maskFromCorner(mask: PodTemplateMask, corner: Corner, x: number, y: number): PodTemplateMask {
  const right = mask.x + mask.width;
  const bottom = mask.y + mask.height;
  if (corner === "north-west") return { x, y, width: right - x, height: bottom - y };
  if (corner === "north-east") return { x: mask.x, y, width: x - mask.x, height: bottom - y };
  if (corner === "south-west") return { x, y: mask.y, width: right - x, height: y - mask.y };
  return { x: mask.x, y: mask.y, width: x - mask.x, height: y - mask.y };
}

export function TemplateCalibrationCanvas({ template, calibration, disabled = false, onChange }: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState(480);
  const [imageState, setImageState] = useState<"loading" | "ready" | "error">("loading");
  const templateImageUrl = usePodAssetUrl(template.preview_url || template.original_url);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const observer = new ResizeObserver(([entry]) => {
      setViewportWidth(Math.max(240, Math.floor((entry?.contentRect.width ?? viewport.clientWidth) - 24)));
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => setImageState("loading"), [template.preview_url]);

  const canvas = useMemo(() => {
    const sourceWidth = Math.max(1, template.width || 1);
    const sourceHeight = Math.max(1, template.height || sourceWidth);
    const width = Math.min(560, viewportWidth);
    const height = Math.min(440, Math.max(240, width * (sourceHeight / sourceWidth)));
    return { width: Math.round(width), height: Math.round(height) };
  }, [template.height, template.width, viewportWidth]);

  const updateMaskPosition = (event: KonvaEventObject<DragEvent>) => {
    const next = clampTemplateCalibration({
      ...calibration,
      mask: {
        ...calibration.mask,
        x: event.target.x() / canvas.width,
        y: event.target.y() / canvas.height,
      },
    });
    onChange(next);
  };

  const updateCorner = (corner: Corner, event: KonvaEventObject<DragEvent>) => {
    const nextMask = maskFromCorner(
      calibration.mask,
      corner,
      event.target.x() / canvas.width,
      event.target.y() / canvas.height,
    );
    onChange(clampTemplateCalibration({ ...calibration, mask: nextMask }));
  };

  const updateAnchor = (event: KonvaEventObject<DragEvent>) => {
    onChange(clampTemplateCalibration({
      ...calibration,
      anchor: { x: event.target.x() / canvas.width, y: event.target.y() / canvas.height },
    }));
  };

  return (
    <div ref={viewportRef} className="pod-calibration-viewport" aria-label="模板蒙版与锚点标定画布">
      <div className="pod-calibration-surface" style={{ width: canvas.width, height: canvas.height }}>
        {templateImageUrl && <img
          src={templateImageUrl}
          alt={`${template.name} 模板`}
          draggable={false}
          decoding="async"
          onLoad={() => setImageState("ready")}
          onError={() => setImageState("error")}
        />}
        {imageState === "loading" && <span className="pod-calibration-image-state">模板加载中…</span>}
        {imageState === "error" && <span className="pod-calibration-image-state is-error">模板图片加载失败</span>}
        <Stage width={canvas.width} height={canvas.height} className="pod-calibration-stage">
          <Layer>
            <Rect width={canvas.width} height={canvas.height} fill="rgba(0,0,0,.22)" listening={false} />
            <Rect
              x={calibration.mask.x * canvas.width}
              y={calibration.mask.y * canvas.height}
              width={calibration.mask.width * canvas.width}
              height={calibration.mask.height * canvas.height}
              fill="rgba(50, 198, 190, .24)"
              stroke="#20d2c3"
              strokeWidth={2}
              dash={[8, 5]}
              draggable={!disabled}
              onDragMove={updateMaskPosition}
              onDragEnd={updateMaskPosition}
            />
            {(["north-west", "north-east", "south-east", "south-west"] as Corner[]).map((corner) => {
              const point = cornerPoint(calibration.mask, corner);
              return (
                <Circle
                  key={corner}
                  x={point.x * canvas.width}
                  y={point.y * canvas.height}
                  radius={HANDLE_RADIUS}
                  fill="#ffffff"
                  stroke="#0877d3"
                  strokeWidth={2}
                  draggable={!disabled}
                  onDragMove={(event) => updateCorner(corner, event)}
                  onDragEnd={(event) => updateCorner(corner, event)}
                />
              );
            })}
            <Line
              points={[calibration.anchor.x * canvas.width - 14, calibration.anchor.y * canvas.height, calibration.anchor.x * canvas.width + 14, calibration.anchor.y * canvas.height]}
              stroke="#ffcf4a"
              strokeWidth={2}
              listening={false}
            />
            <Line
              points={[calibration.anchor.x * canvas.width, calibration.anchor.y * canvas.height - 14, calibration.anchor.x * canvas.width, calibration.anchor.y * canvas.height + 14]}
              stroke="#ffcf4a"
              strokeWidth={2}
              listening={false}
            />
            <Circle
              x={calibration.anchor.x * canvas.width}
              y={calibration.anchor.y * canvas.height}
              radius={6}
              fill="#ffcf4a"
              stroke="#493500"
              strokeWidth={2}
              draggable={!disabled}
              onDragMove={updateAnchor}
              onDragEnd={updateAnchor}
            />
          </Layer>
        </Stage>
      </div>
      <div className="pod-calibration-legend">
        <span><i className="is-mask" />印花蒙版</span>
        <span><i className="is-anchor" />设计锚点</span>
        <small>{disabled ? "系统模板标定只读" : "拖动蒙版、四角和锚点微调"}</small>
      </div>
    </div>
  );
}
