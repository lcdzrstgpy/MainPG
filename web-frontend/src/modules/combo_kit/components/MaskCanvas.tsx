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
  const [inverted, setInverted] = useState(item.mask_inverted);
  const [points, setPoints] = useState<Point[]>(() => readMask(item.mask_json) ?? DEFAULT_POINTS);
  const [view, setView] = useState<'view' | 'mask'>('view');
  const [originName, setOriginName] = useState('');

  useEffect(() => {
    const raw = item.original_url || item.original_path || '';
    setOriginName(raw.split('/').pop() || '');
  }, [item]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    draw(canvas, points, inverted, imageRef.current);
  }, [points, inverted, view]);

  const localPoint = (e: React.PointerEvent<HTMLCanvasElement>): Point => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  };

  const nearHandle = (p: Point): number => {
    return points.findIndex(([x, y]) => Math.hypot(x - p[0], y - p[1]) < 0.12);
  };

  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const idx = nearHandle(localPoint(e));
    dragRef.current = idx;
    if (idx >= 0) {
      const p = localPoint(e);
      setPoints((pts) => pts.map((q, i) => (i === idx ? p : q)));
    }
  };

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current < 0) return;
    const idx = dragRef.current;
    const p = localPoint(e);
    setPoints((pts) => pts.map((q, i) => (i === idx ? p : q)));
  };

  const onUp = () => {
    dragRef.current = -1;
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
                const img = e.currentTarget;
                const canvas = canvasRef.current;
                if (!canvas) return;
                imageRef.current = img;
                canvas.width = img.naturalWidth || 800;
                canvas.height = img.naturalHeight || 800;
                draw(canvas, points, inverted, img);
              }}
              referrerPolicy="no-referrer"
            />
            <canvas
              ref={canvasRef}
              width={800}
              height={800}
              style={{ width: '100%', maxHeight: 560, objectFit: 'contain', border: '1px solid #ccc', cursor: 'crosshair', touchAction: 'none' }}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
            />
            <div className="combo-mask-hint">拖动六个控制点，圈住商品主体；可反选；保存后由 AI 结合主体词解析。</div>
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
  // 原图底图。
  if (image && image.complete && image.naturalWidth) {
    ctx.drawImage(image, 0, 0, w, h);
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
