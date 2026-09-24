/**
 * 顶栏几何量的统一取法。
 *
 * 吸顶（position:fixed）元素的 top 必须贴着顶栏的**视觉**下沿，而这里有个坑：
 * 「顶栏滚动收起」（`html[data-topbar-collapse="on"]`）时主行走 `visibility: hidden`
 * —— 它**仍占位**，所以 `.topbar-card` 的 DOM 高度不变、`getBoundingClientRect().bottom`
 * 纹丝不动；用户看到的「顶栏变矮」其实是标签行 `transform: translateY(-66px)` 上移。
 *
 * 因此不能拿 `.topbar-card` 的 bottom 当顶栏下沿，否则收起后吸顶元素还停在原地
 * （看着像"没贴上去"）。`.topbar-lower-row` 自带 transform，而
 * `getBoundingClientRect()` 返回的是**变换后**的视觉盒，正好等于用户看到的下沿。
 */

/** 顶栏当前的视觉底部（已含收起态的 translateY）；无顶栏时为 0。 */
export function getTopbarVisualBottom(topbar: HTMLElement | null | undefined): number {
  if (!topbar) return 0;
  const lowerRow = topbar.querySelector<HTMLElement>(".topbar-lower-row");
  const rect = (lowerRow ?? topbar).getBoundingClientRect();
  return rect.bottom > 0 ? Math.round(rect.bottom) : 0;
}
