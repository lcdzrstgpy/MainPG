import { useEffect, useState } from "react";

/**
 * 给「点开 / 收起」的浮层用的存在感钩子。
 *
 * 条件渲染（`{open && <div/>}`）只能瞬间出现、瞬间消失；这里把卸载推迟到退出动画
 * 播完之后，并多给一帧让入场动画有起始状态可插值。
 *
 * 用法：
 * ```tsx
 * const { mounted, open: shown } = useFadePresence(open);
 * return mounted ? <div className="xxx-popover" data-open={shown ? "true" : "false"}>…</div> : null;
 * ```
 * 配合 global.css 里的 `.ui-popover[data-open="false"]` 过渡即可。
 *
 * @param open    业务上的开关状态
 * @param exitMs  退出动画时长，需与 CSS 里的过渡时长一致（默认 170ms）
 */
export function useFadePresence(open: boolean, exitMs = 170) {
  const [mounted, setMounted] = useState(open);
  // 挂载后先渲染成收起态，下一帧再打开，避免「以最终样式直接插入」导致没有入场动画。
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      const frame = window.requestAnimationFrame(() => setVisible(true));
      return () => window.cancelAnimationFrame(frame);
    }
    setVisible(false);
    if (!mounted) return;
    const timer = window.setTimeout(() => setMounted(false), exitMs);
    return () => window.clearTimeout(timer);
  }, [open, exitMs, mounted]);

  return { mounted, open: visible };
}
