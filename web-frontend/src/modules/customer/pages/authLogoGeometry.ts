/** 登录页 logo 共享元素过渡的纯几何计算（无 React 依赖，便于单测）。 */

export type LogoRect = { left: number; top: number; width: number };

export type LogoInvert = { dx: number; dy: number; scale: number };

/**
 * 计算 FLIP 的「反演」变换：把已经落在新位置的 logo 先摆回旧位置，
 * 下一帧再放行，由浏览器插值出「同一个 logo 从旧位置移到新位置」的观感。
 *
 * 以左上角为基准：渲染后的左上角 = 新左上角 + (dx, dy)，宽度 = 新宽度 × scale。
 */
export function invertRect(prev: LogoRect, next: LogoRect): LogoInvert {
  return {
    dx: prev.left - next.left,
    dy: prev.top - next.top,
    scale: next.width > 0 ? prev.width / next.width : 1,
  };
}

/** 位移与缩放都小于阈值时无需动画（例如登录 / 注册互切，logo 原地不动）。 */
export function isNegligible(move: LogoInvert): boolean {
  return Math.abs(move.dx) < 0.5 && Math.abs(move.dy) < 0.5 && Math.abs(move.scale - 1) < 0.001;
}
