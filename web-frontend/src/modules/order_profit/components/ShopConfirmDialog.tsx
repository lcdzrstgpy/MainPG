import { useEffect } from "react";

export type ConfirmShop = {
  name: string;
  store_code?: string;
  code_status?: string;
};

/** 采集前的店铺确认弹窗：采错店铺会把数据算到别人名下，所以每次采集前确认一次。 */
export function ShopConfirmDialog({
  open,
  actionLabel,
  shop,
  username,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  actionLabel: string;
  shop: ConfirmShop | null;
  username: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const name = shop?.name || "未绑定店铺";
  const code = shop?.store_code || "";

  return (
    <div className="op-modal-mask" role="dialog" aria-modal="true" aria-label="确认采集店铺">
      <div className="op-modal">
        <div className="op-modal-head">
          <strong>确认采集店铺</strong>
          <span className="op-chip">{actionLabel}</span>
        </div>
        <div className="op-modal-body">
          <div className="op-modal-row">
            <span>账号</span>
            <strong>{username || "本机"}</strong>
          </div>
          <div className="op-modal-row">
            <span>店铺</span>
            <strong>
              {name}
              {code ? `（${code}）` : ""}
            </strong>
          </div>
          <p className="op-modal-note">采到的订单、流量、销量、结算、财务都会归到这家店。</p>
          {shop?.code_status === "local" ? (
            <p className="op-modal-warn">这家店还是本地临时码，联网后可在店铺列表同步为正式码。</p>
          ) : null}
        </div>
        <div className="op-modal-actions">
          <button type="button" className="op-btn" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="op-btn op-btn-primary" onClick={onConfirm}>
            确认并开始
          </button>
        </div>
      </div>
    </div>
  );
}
