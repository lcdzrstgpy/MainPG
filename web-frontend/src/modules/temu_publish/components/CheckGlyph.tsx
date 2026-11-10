/**
 * 步骤/区块「已完成」的对勾。
 *
 * 不用字符「✓」：笔画粗细、垂直居中全靠字体，换台机器或换主题字体就会歪，
 * 在 18~26px 的小圆里尤其明显（浏览器还会把不同字体的 ✓ 画成不同比例）。
 * 这里用一条圆头折线的 SVG：粗细/圆角/居中都由我们控制，颜色继承 currentColor。
 * 类名别叫 temu-check —— 那个类名已经被「勾选卡片」占用（min-height:34px + 虚线框），
 * 套上去 SVG 会被撑成一块白框（2026-11-09 踩过）。
 */
export function CheckGlyph() {
  return (
    <svg className="temu-tick" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M3.7 8.5 6.55 11.3 12.3 5.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
