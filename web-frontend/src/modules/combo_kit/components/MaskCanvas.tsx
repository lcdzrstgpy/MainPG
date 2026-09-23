import { useEffect, useRef, useState } from 'react';
import { comboKitOriginUrl, type ComboKitItem } from '../../product_processing/api/comboKitApi';

type Props = {
  setId: string;
  item: ComboKitItem;
  onSaveMask?: (itemId: string, mask: { points: Array<[number, number]> }, inverted: boolean) => void;
  // 算法预框选：返回主体轮廓多边形（失败/不可信返回 null，由本组件回落默认六边形）。
  onAutoMask?: (itemId: string) => Promise<Point[] | null>;
};

type Point = [number, number];

// 兜底框选：还没做自动分割（或分割不可信）时用这个固定多边形，用户可拖动微调。
// 坐标归一化 0..1。
const DEFAULT_POINTS: Point[] = [
  [0.5, 0.06],
  [0.94, 0.32],
  [0.94, 0.68],
  [0.5, 0.94],
  [0.06, 0.68],
  [0.06, 0.32],
];

const HANDLE_R = 9;

export function MaskCanvas({ setId, item, onSaveMask, onAutoMask }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<number>(-1);
  const dragWholeRef = useRef<{ start: Point; orig: Point[] } | null>(null);
  // 记录正在自动框选的 item_id：切图后旧请求的结果必须丢弃，不能覆盖新图的框。
  const autoBusyRef = useRef<string>('');
  const [inverted, setInverted] = useState(item.mask_inverted);
  const [points, setPoints] = useState<Point[]>(() => readMask(item.mask_json) ?? DEFAULT_POINTS);
  const [view, setView] = useState<'view' | 'mask'>('view');
  const [originName, setOriginName] = useState('');
  const [autoState, setAutoState] = useState<'idle' | 'running' | 'done' | 'failed'>('idle');

  useEffect(() => {
    const raw = item.original_url || item.original_path || '';
    setOriginName(raw.split('/').pop() || '');
  }, [item]);

  // 算法预框选：拿到轮廓就直接当初始框，失败则由下面的默认多边形兜底。
  const runAutoMask = async (itemId: string) => {
    if (!onAutoMask || autoBusyRef.current) return;
    autoBusyRef.current = itemId;
    setAutoState('running');
    try {
      const auto = await onAutoMask(itemId);
      // 结果回来时若已切到别的图，直接丢弃。
      if (autoBusyRef.current !== itemId) return;
      if (auto && auto.length >= 3) {
        setPoints(auto);
        setView('mask');
        setAutoState('done');
        return;
      }
      setAutoState('failed');
    } catch {
      if (autoBusyRef.current === itemId) setAutoState('failed');
    } finally {
      if (autoBusyRef.current === itemId) autoBusyRef.current = '';
    }
  };

  // 每张图片的蒙版独立：切换图片（item_id 变化）时，重置为当前图自己的蒙版/反选，
  // 避免继承上一张的形状。保存蒙版后 item_id 不变，不会覆盖用户刚绘制的蒙版。
  useEffect(() => {
    setInverted(item.mask_inverted);
    const saved = readMask(item.mask_json);
    setPoints(saved ?? DEFAULT_POINTS);
    if (saved || !onAutoMask) {
      setAutoState('idle');
      return;
    }
    // 还没有人工蒙版：先跑一次算法预框选，用户只需在此基础上微调。
    void runAutoMask(item.item_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // 命中半径随点数收窄：自动框选可能给出 10+ 个点，半径过大相邻控制点会互相抢。
    const radius = Math.min(0.1, 0.6 / Math.max(3, points.length));
    return points.findIndex(([x, y]) => Math.hypot(x - p[0], y - p[1]) < radius);
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
    setAutoState('idle');
  };

  const save = () => {
    onSaveMask?.(item.item_id, { points }, inverted);
  };

  const autoHint =
    autoState === 'running'
      ? '正在自动识别主体…'
      : autoState === 'done'
        ? '已自动框选，拖动控制点微调后保存即可。'
        : autoState === 'failed'
          ? '未能自动识别主体，请手动拖动控制点框选。'
          : '';

  return (
    <div className="combo-mask">
      <div className="combo-mask-toolbar">
        <button className="btn-mini" onClick={() => setView(view === 'view' ? 'mask' : 'view')}>
          {view === 'view' ? '框选蒙版' : '查看原图'}
        </button>
        <button className="btn-mini" onClick={() => setInverted((v) => !v)}>
          反选：{inverted ? '是' : '否'}
        </button>
        {onAutoMask && (
          <button
            className="btn-mini"
            disabled={autoState === 'running'}
            onClick={() => void runAutoMask(item.item_id)}
          >
            {autoState === 'running' ? '识别中…' : '自动框选'}
          </button>
        )}
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
            <div className="combo-mask-hint">
              {autoHint || '拖动控制点圈住商品主体，或在框内拖动可整体平移整框；可反选；保存后由 AI 结合主体词解析。'}
            </div>
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
  // 多边形填充。
  ctx.fillStyle = inverted ? 'rgba(0,0,0,0.45)' : 'rgba(30,190,120,0.35)';
  polygon(ctx, px);
  ctx.fill();
  // 边线。
  ctx.strokeStyle = inverted ? '#9ca3af' : '#1f7a46';
  ctx.lineWidth = 2;
  polygon(ctx, px);
  ctx.stroke();
  // 各控制点。
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
  // 点数不固定：人工绘制是 6 点六边形，自动框选可能给出 10+ 点的贴合轮廓。
  return arr.length >= 3 ? arr : null;
}
