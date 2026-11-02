import { useEffect } from "react";

import type { TeamRollupPreview } from "../types";

/** 提交团队汇总前的确认弹窗：先让人看清「哪些店铺」提交到「哪个服务器账号」。 */
export function TeamRollupDialog({
  open,
  preview,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  preview: TeamRollupPreview | null;
  busy: boolean;
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

  if (!open || !preview) return null;

  const account = preview.account || {};
  const shops = preview.stores || [];
  const names = shops.map((shop) => shop.name).filter(Boolean);

  return (
    <div className="op-modal-mask" role="dialog" aria-modal="true" aria-label="确认提交团队汇总">
      <div className="op-modal op-modal-lg">
        <div className="op-modal-head">
          <strong>确认提交团队汇总</strong>
          <span className="op-chip">
            {preview.week_start} ~ {preview.week_end}
          </span>
        </div>
        <div className="op-modal-body">
          <div className="op-modal-row">
            <span>提交到账号</span>
            <strong>{account.username || "当前登录账号"}</strong>
          </div>
          <div className="op-modal-row">
            <span>本账号店铺</span>
            <strong>{names.length ? `${names.length} 家：${names.join("、")}` : "暂无店铺"}</strong>
          </div>
          <div className="op-modal-row">
            <span>提交范围</span>
            <strong>
              汇总 {preview.day_count} 天 · 采集批次 {preview.batch_count} 个
            </strong>
          </div>
          {shops.length ? (
            <div className="op-rollup-shops">
              {shops.map((shop) => (
                <div className="op-rollup-shop" key={shop.store_code || shop.name}>
                  <strong>{shop.name}</strong>
                  <span>{shop.store_code || "暂无店铺码"}</span>
                  <em>
                    {shop.day_count} 天
                    {shop.last_date ? ` · 至 ${shop.last_date}` : " · 暂无数据"}
                  </em>
                </div>
              ))}
            </div>
          ) : null}
          {preview.missing_code_shops.length ? (
            <p className="op-modal-warn">
              {preview.missing_code_shops.join("、")} 还没有店铺码，提交时会先尝试联网补登记；仍失败的话本次不会提交这几家。
            </p>
          ) : null}
          <p className="op-modal-note">提交后团队看板会按「店铺 × 日期」覆盖更新，重复提交不会产生重复数据。</p>
        </div>
        <div className="op-modal-actions">
          <button type="button" className="op-btn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button type="button" className="op-btn op-btn-primary" onClick={onConfirm} disabled={busy}>
            {busy ? "提交中…" : "确认提交"}
          </button>
        </div>
      </div>
    </div>
  );
}
