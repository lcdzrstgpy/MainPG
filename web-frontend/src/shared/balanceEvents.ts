/** 全局余额变更事件：领取/充值/结算后触发，悬浮球等常驻组件据此立即刷新。 */
export const BALANCE_CHANGED_EVENT = "billing:balance-changed";

export function notifyBalanceChanged(): void {
  window.dispatchEvent(new CustomEvent(BALANCE_CHANGED_EVENT));
}
