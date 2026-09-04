import { useEffect, useRef, useState } from 'react';
import { comboKitOriginUrl, type ComboKitItem } from '../../product_processing/api/comboKitApi';

type Props = {
  setId: string;
  item: ComboKitItem;
  onSaveMask?: (itemId: string, mask: { points: Array<[number, number]> }, inverted: boolean) => void;
};

type Point = [number, number];

// 六点框选：6 个可拖动控制点围成六边形，包围商品主体。坐标归一化 0..1。
const DEFAULT_POINTS: Point[] = [
  [0.5, 0.06],
  [0.94, 0.32],
  [0.94, 0.68],
  [0.5, 0.94],
  [0.06, 0.68],
  [0.06, 0.32],
];

const HANDLE_R = 9;

export function MaskCanvas({ setId, item, onSaveMask }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<number>(-1);
  const dragWholeRef = useRef<{ start: Point; orig: Point[] } | null>(null);
  const [inverted, setInverted] = useState(item.mask_inverted);
  const [points, setPoints] = useState<Point[]>(() => readMask(item.mask_json) ?? DEFAULT_POINTS);
  const [view, setView] = useState<'view' | 'mask'>('view');
  const [originName, setOriginName] = useState('');

  useEffect(() => {
    const raw = item.original_url || item.original_path || '';
    setOriginName(raw.split('/').pop() || '');
  }, [item]);

  // 每张图片的蒙版独立：切换图片（item_id 变化）时，重置为当前图自己的蒙版/反选，
  // 避免继承上一张的形状。保存蒙版后 item_id 不变，不会覆盖用户刚绘制的蒙版。
  useEffect(() => {
    setInverted(item.mask_inverted);
    setPoints(readMask(item.mask_json) ?? DEFAULT_POINTS);
  }, [item.item_id]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    draw(canvas, points, inverted, imageRef.current);
  }, [points, inverted, view]);

  // 让 canvas 内部像素尺寸跟随其显示尺寸，使画布坐标与屏幕坐标 1:1 对应，
  // 彻底避免 objectFit:contain 的留边/缩放导致坐标换算偏移（点“框外”却命中）。
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      canvas.width = Math.round(rect.width);
      canvas.height = Math.round(rect.height);
      draw(canvas, points, inverted, imageRef.current);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [points, inverted, view]);

  const localPoint = (e: React.PointerEvent<HTMLCanvasElement>): Point => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return [0, 0];
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  };

  const nearHandle = (p: Point): number => {
    return points.findIndex(([x, y]) => Math.hypot(x - p[0], y - p[1]) < 0.10);
  };

  const pointInPoly = (p: Point): boolean => {
    let inside = false;
    const n = points.length;
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const [xi, yi] = points[i];
      const [xj, yj] = points[j];
      const intersect = yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  };

  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const p = localPoint(e);
    const idx = nearHandle(p);
    if (idx >= 0) {
      dragRef.current = idx;
      setPoints((pts) => pts.map((q, i) => (i === idx ? p : q)));
      return;
    }
    // 在多边形内部按下：整体平移整框（每个点保持相对位置，仅偏移坐标）。
    if (pointInPoly(p)) {
      dragWholeRef.current = { start: p, orig: points.map((q) => [q[0], q[1]] as Point) };
    }
  };

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = localPoint(e);
    // 整体平移：以按下点与当前点之差作为所有点的共同位移，并夹取到 0..1。
    if (dragWholeRef.current) {
      const { start, orig } = dragWholeRef.current;
      const dx = p[0] - start[0];
      const dy = p[1] - start[1];
      setPoints(orig.map(([x, y]) => [
        Math.max(0, Math.min(1, x + dx)),
        Math.max(0, Math.min(1, y + dy)),
      ]));
      return;
    }
    if (dragRef.current < 0) return;
    const idx = dragRef.current;
    setPoints((pts) => pts.map((q, i) => (i === idx ? p : q)));
  };

  const onUp = () => {
    dragRef.current = -1;
    dragWholeRef.current = null;
  };

  const reset = () => {
    setPoints(DEFAULT_POINTS);
  };

  const save = () => {
    onSaveMask?.(item.item_id, { points }, inverted);
  };

  return (
    <div className="combo-mask">
      <div className="combo-mask-toolbar">
        <button className="btn-mini" onClick={() => setView(view === 'view' ? 'mask' : 'view')}>
          {view === 'view' ? '框选蒙版' : '查看原图'}
        </button>
        <button className="btn-mini" onClick={() => setInverted((v) => !v)}>
          反选：{inverted ? '是' : '否'}
        </button>
        <button className="btn-mini danger" onClick={reset}>重置</button>
        <button className="btn-mini primary" onClick={save}>保存蒙版</button>
      </div>
      <div className="combo-mask-stage">
        {view === 'view' && originName && (
          <img
            src={comboKitOriginUrl(setId, originName)}
            alt={item.subject_keywords || '原图'}
            style={{ width: '100%', maxHeight: 560, objectFit: 'contain' }}
            referrerPolicy="no-referrer"
          />
        )}
        {view === 'mask' && (
          <>
            <img
              src={originName ? comboKitOriginUrl(setId, originName) : ''}
              alt="蒙版底图"
              style={{ display: 'none' }}
              onLoad={(e) => {
                // 只登记图像对象；画布尺寸由下方 ResizeObserver 跟随显示区域设定，
                // 保证画布坐标与屏幕坐标 1:1，避免 objectFit 留边导致命中偏移。
                imageRef.current = e.currentTarget;
                const canvas = canvasRef.current;
                if (canvas) draw(canvas, points, inverted, e.currentTarget);
              }}
              referrerPolicy="no-referrer"
            />
            <canvas
              ref={canvasRef}
              style={{ width: '100%', maxHeight: 560, display: 'block', border: '1px solid #ccc', cursor: 'crosshair', touchAction: 'none' }}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
            />
            <div className="combo-mask-hint">拖动六个控制点圈住商品主体，或在框内拖动可整体平移整框；可反选；保存后由 AI 结合主体词解析。</div>
          </>
        )}
      </div>
    </div>
  );
}

function draw(canvas: HTMLCanvasElement, points: Point[], inverted: boolean, image: HTMLImageElement | null) {
  const ctx = canvas.getContext('2d')!;
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  // 原图底图：等比缩放居中铺入画布（contain），避免拉伸变形。
  if (image && image.complete && image.naturalWidth) {
    const scale = Math.min(w / image.naturalWidth, h / image.naturalHeight);
    const dw = image.naturalWidth * scale;
    const dh = image.naturalHeight * scale;
    const dx = (w - dw) / 2;
    const dy = (h - dh) / 2;
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(image, dx, dy, dw, dh);
  } else {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
  }
  if (!points.length) return;
  const px = points.map(([x, y]) => [x * w, y * h] as [number, number]);
  ctx.globalCompositeOperation = 'source-over';
  // 六边形填充。
  ctx.fillStyle = inverted ? 'rgba(0,0,0,0.45)' : 'rgba(30,190,120,0.35)';
  polygon(ctx, px);
  ctx.fill();
  // 边线。
  ctx.strokeStyle = inverted ? '#9ca3af' : '#1f7a46';
  ctx.lineWidth = 2;
  polygon(ctx, px);
  ctx.stroke();
  // 六个控制点。
  for (const [x, y] of px) {
    ctx.beginPath();
    ctx.arc(x, y, HANDLE_R, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.strokeStyle = '#1f7a46';
    ctx.lineWidth = 2;
    ctx.stroke();
  }
}

function polygon(ctx: CanvasRenderingContext2D, px: Array<[number, number]>) {
  ctx.beginPath();
  px.forEach(([x, y], idx) => {
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

function readMask(mask: Record<string, unknown>): Point[] | null {
  const pts = mask?.points;
  if (!Array.isArray(pts)) return null;
  const arr = pts
    .filter((p) => Array.isArray(p) && p.length === 2)
    .map((p) => [Number(p[0]), Number(p[1])] as Point);
  return arr.length === 6 ? arr : null;
}
