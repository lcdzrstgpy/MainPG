import { useEffect, useId, useRef, useState } from 'react';

/** 进度平滑插值：轮询返回的是整数进度，直接渲染会一格一格跳。
 *  用 requestAnimationFrame 做指数缓动，让圆环与数字连续推进；
 *  缓动状态收在组件内部，逐帧重渲染只影响这个圆环，不拖累整页。 */
function useSmoothProgress(target: number): number {
  const [value, setValue] = useState(target);
  const valueRef = useRef(target);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const diff = target - valueRef.current;
      if (Math.abs(diff) < 0.05) {
        valueRef.current = target;
        setValue(target);
        return;
      }
      valueRef.current += diff * 0.12;
      setValue(valueRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return value;
}

type TaskProgressRingProps = {
  /** 任务名，显示在圆环内部百分比下方 */
  label: string;
  /** 已完成数量 */
  current?: number;
  /** 总数量，<=0 表示总量未知 */
  total?: number;
  /** 是否进行中；false 表示已完成态 */
  running: boolean;
};

/** 组合生图任务的环形进度：沿用「AI 处理」页的圆环视觉。
 *  后端生图为并发执行、过程不上报增量，此时 current 一直为 0，
 *  这类情况退化为「环形流动」的不确定态，直观表达「正在跑」。 */
export function TaskProgressRing({ label, current = 0, total = 0, running }: TaskProgressRingProps) {
  const gradientId = `combo-progress-gradient-${useId().replace(/:/g, '')}`;
  const determinate = total > 0 && current > 0;
  const percent = determinate ? Math.round((current / total) * 100) : 0;
  const smooth = useSmoothProgress(percent);
  const indeterminate = running && !determinate;

  return (
    <div
      className={`combo-progress-ring ${running ? 'is-live' : 'is-done'}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={determinate ? percent : undefined}
    >
      <span className="combo-progress-halo" aria-hidden="true" />
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#2fe0e6" />
            <stop offset="55%" stopColor="#0baec0" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
        </defs>
        <circle className="combo-progress-track" cx="60" cy="60" r="52" pathLength="100" />
        {indeterminate ? (
          <circle className="combo-progress-sweep" cx="60" cy="60" r="52" pathLength="100" aria-hidden="true" />
        ) : (
          <circle
            className="combo-progress-value"
            cx="60"
            cy="60"
            r="52"
            pathLength="100"
            stroke={`url(#${gradientId})`}
            strokeDashoffset={100 - smooth}
          />
        )}
      </svg>
      <div className="combo-progress-center">
        <strong>{determinate ? Math.round(smooth) : '···'}{determinate && <em>%</em>}</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}

export default TaskProgressRing;
