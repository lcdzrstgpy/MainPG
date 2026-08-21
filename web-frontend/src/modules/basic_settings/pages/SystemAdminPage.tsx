import { useCallback, useEffect, useMemo, useState } from "react";

import {
  loadKeyGrants,
  loadPodPricingItems,
  loadPricingChangelog,
  loadPricingItems,
  updatePodPricingItems,
  updatePricingItems,
  type KeyGrant,
  type PricingChangelogEntry,
  type PricingSubItem,
} from "../api/systemAdminApi";
import "../styles/basicSettings.css";

const SUBITEM_LABELS: Record<string, string> = {
  title: "标题",
  description: "描述",
  product_dimensions: "尺寸",
  four_grid: "智能生图",
  detail_images: "详情图",
};

const SUBITEM_ORDER = ["title", "description", "product_dimensions", "four_grid", "detail_images"];

const FEATURE_KEYS = SUBITEM_ORDER;

const POD_FEATURE_LABELS: Record<string, string> = {
  "pod.title": "豆包标题",
  "pod.image": "POD 图片",
};

const POD_FEATURE_KEYS = ["pod.title", "pod.image"];

type Tab = "pricing" | "changelog" | "keys";

function formatTime(value: string): string {
  if (!value) return "-";
  return value.replace("T", " ").replace("Z", "").slice(0, 19);
}

function summariseItems(after: unknown): string[] {
  const root = after as { items?: Record<string, { charge_points?: number }> } | null;
  const items = root?.items;
  if (!items) return [];
  return FEATURE_KEYS
    .filter((key) => items[key] != null)
    .map((key) => `${SUBITEM_LABELS[key] ?? key} ${items[key]?.charge_points ?? "-"}分`);
}

export function SystemAdminPage() {
  const [tab, setTab] = useState<Tab>("pricing");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  // 定价管理
  const [ruleVersion, setRuleVersion] = useState(0);
  const [items, setItems] = useState<Record<string, PricingSubItem>>({});
  const [maxChargePerLink, setMaxChargePerLink] = useState(0);
  const [freezePerLink, setFreezePerLink] = useState(0);
  const [ttlDays, setTtlDays] = useState(7);
  const [editOpen, setEditOpen] = useState(false);
  const [draftItems, setDraftItems] = useState<Record<string, number>>({});
  const [changeReason, setChangeReason] = useState("");
  const [podRuleVersion, setPodRuleVersion] = useState(0);
  const [podItems, setPodItems] = useState<Record<string, PricingSubItem>>({});
  const [podEditOpen, setPodEditOpen] = useState(false);
  const [podDraftItems, setPodDraftItems] = useState<Record<string, number>>({});
  const [podChangeReason, setPodChangeReason] = useState("");

  // 变更日志 / 密钥发放
  const [changelog, setChangelog] = useState<PricingChangelogEntry[]>([]);
  const [grants, setGrants] = useState<KeyGrant[]>([]);

  const refreshPricing = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const [payload, podPayload] = await Promise.all([loadPricingItems(), loadPodPricingItems()]);
      setRuleVersion(payload.pricing.rule_version);
      setItems(payload.pricing.items ?? {});
      setMaxChargePerLink(payload.pricing.max_charge_per_link);
      setFreezePerLink(payload.pricing.freeze_per_link);
      setTtlDays(payload.pricing.ttl_days);
      setPodRuleVersion(podPayload.pricing.rule_version);
      setPodItems(podPayload.pricing.items as Record<string, PricingSubItem>);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshChangelog = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const payload = await loadPricingChangelog(100);
      setChangelog(payload.items ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshGrants = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const payload = await loadKeyGrants(100);
      setGrants(payload.items ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refreshPricing();
  }, [refreshPricing]);

  const selectTab = (next: Tab) => {
    setTab(next);
    setMessage("");
    setError("");
    if (next === "changelog") void refreshChangelog();
    if (next === "keys") void refreshGrants();
  };

  const openEdit = () => {
    const draft: Record<string, number> = {};
    for (const key of FEATURE_KEYS) {
      draft[key] = items[key]?.charge_points ?? 0;
    }
    setDraftItems(draft);
    setChangeReason("");
    setMessage("");
    setError("");
    setEditOpen(true);
  };

  const draftTotal = useMemo(() => Object.values(draftItems).reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0), [draftItems]);

  const openPodEdit = () => {
    setPodDraftItems(Object.fromEntries(POD_FEATURE_KEYS.map((key) => [key, podItems[key]?.charge_points ?? 0])));
    setPodChangeReason("");
    setMessage("");
    setError("");
    setPodEditOpen(true);
  };

  const savePricing = async () => {
    const reason = changeReason.trim();
    if (!reason) {
      setError("请填写变更原因（必填，将写入审计日志）");
      return;
    }
    if (draftTotal < 35 || draftTotal > 45) {
      setError(`单条链接总价须在 35~45 积分之间，当前为 ${draftTotal} 积分`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await updatePricingItems({
        items: Object.fromEntries(
          FEATURE_KEYS.map((key) => [key, { charge_points: draftItems[key] ?? 0 }]),
        ),
        change_reason: reason,
      });
      setRuleVersion(payload.pricing.rule_version);
      setItems(payload.pricing.items ?? {});
      setMaxChargePerLink(payload.pricing.max_charge_per_link);
      setFreezePerLink(payload.pricing.freeze_per_link);
      setTtlDays(payload.pricing.ttl_days);
      setEditOpen(false);
      setMessage(`定价已更新（规则版本 v${payload.pricing.rule_version}）`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const savePodPricing = async () => {
    const reason = podChangeReason.trim();
    if (!reason) {
      setError("请填写 POD 定价变更原因（必填，将写入审计日志）");
      return;
    }
    if (POD_FEATURE_KEYS.some((key) => !Number.isFinite(podDraftItems[key]) || podDraftItems[key] < 0)) {
      setError("POD 调用单价必须是大于或等于 0 的数字");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await updatePodPricingItems({
        items: Object.fromEntries(
          POD_FEATURE_KEYS.map((key) => [key, { charge_points: podDraftItems[key] ?? 0 }]),
        ),
        change_reason: reason,
      });
      setPodRuleVersion(payload.pricing.rule_version);
      setRuleVersion(payload.pricing.rule_version);
      setPodItems(payload.pricing.items as Record<string, PricingSubItem>);
      setPodEditOpen(false);
      setMessage(`POD 定价已更新（规则版本 v${payload.pricing.rule_version}）`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="settings-page">
      <section className="settings-hero">
        <span className="settings-hero-icon iconfont icon-key" aria-hidden="true" />
        <div>
          <p className="eyebrow">SYSTEM ADMIN</p>
          <h1>系统管理</h1>
          <p>定价规则引擎、密钥发放记录与变更审计日志。所有定价调整均由服务端计算并以版本+日志完整留痕。</p>
        </div>
      </section>

      <nav className="settings-tabs" role="tablist" aria-label="系统管理分区">
        {([
          ["pricing", "定价管理"],
          ["changelog", "变更日志"],
          ["keys", "密钥发放"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "is-active" : ""}
            onClick={() => selectTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>

      {message && <p className="settings-status is-success">{message}</p>}
      {error && <p className="settings-status is-error" role="alert">{error}</p>}

      {tab === "pricing" && (<>
        <section className="settings-card settings-card-wide" aria-label="定价规则">
          <div className="settings-card-head">
            <div>
              <h3>子项单价（单条链接合计须在 35~45 积分）</h3>
              <p className="settings-card-description">
                成功子项扣全价；有返回但质量门拦截退一半；上游无返回全退。修改会生成新规则版本并写入审计日志。
              </p>
            </div>
            <button type="button" className="settings-secondary-button" onClick={openEdit} disabled={busy}>
              编辑定价
            </button>
          </div>
          <div className="settings-summary-bar">
            <span>当前规则版本：<strong>v{ruleVersion}</strong></span>
            <span>单条最高：<strong>{maxChargePerLink} 积分</strong></span>
            <span>冻结单价：<strong>{freezePerLink} 积分/条</strong></span>
            <span>冻结 TTL：<strong>{ttlDays} 天自动释放</strong></span>
          </div>
          <table className="settings-table">
            <thead>
              <tr>
                <th>子项</th>
                <th>单价（积分）</th>
                <th>质量门拦截退款</th>
                <th>无返回退款</th>
              </tr>
            </thead>
            <tbody>
              {FEATURE_KEYS.map((key) => {
                const item = items[key];
                if (!item) return null;
                return (
                  <tr key={key}>
                    <td>{SUBITEM_LABELS[key] ?? key}</td>
                    <td>{item.charge_points} 积分</td>
                    <td>{Math.round(item.intercept_refund_ratio * 100)}%</td>
                    <td>{Math.round(item.no_return_refund_ratio * 100)}%</td>
                  </tr>
                );
              })}
              <tr className="settings-table-total">
                <td>合计</td>
                <td>{maxChargePerLink} 积分</td>
                <td colSpan={2} />
              </tr>
            </tbody>
          </table>
        </section>
        <section className="settings-card settings-card-wide" aria-label="POD AI 调用单价">
          <div className="settings-card-head">
            <div>
              <h3>POD AI 调用单价</h3>
              <p className="settings-card-description">
                标题与图片独立计价；成功调用扣费，上游无返回或预冻结但未执行的调用全退。
              </p>
            </div>
            <button type="button" className="settings-secondary-button" onClick={openPodEdit} disabled={busy}>
              编辑 POD 定价
            </button>
          </div>
          <div className="settings-summary-bar">
            <span>当前规则版本：<strong>v{podRuleVersion}</strong></span>
            <span>积分精度：<strong>0.1 积分</strong></span>
          </div>
          <table className="settings-table">
            <thead><tr><th>调用项</th><th>单价（积分）</th><th>无返回退款</th></tr></thead>
            <tbody>
              {POD_FEATURE_KEYS.map((key) => (
                <tr key={key}>
                  <td>{POD_FEATURE_LABELS[key]}<small>{key}</small></td>
                  <td>{podItems[key]?.charge_points ?? "未配置"}</td>
                  <td>100%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </>)}

      {tab === "changelog" && (
        <section className="settings-card settings-card-wide" aria-label="变更日志">
          <div className="settings-card-head">
            <div>
              <h3>定价变更审计日志</h3>
              <p className="settings-card-description">只追加、不可删除；记录操作人、原因与前后完整定价。</p>
            </div>
          </div>
          {changelog.length === 0 ? (
            <p className="settings-empty">暂无变更记录。</p>
          ) : (
            <table className="settings-table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>操作人</th>
                  <th>变更原因</th>
                  <th>变更后子项</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {changelog.map((entry) => (
                  <tr key={entry.id}>
                    <td>v{entry.rule_version}</td>
                    <td>{entry.changed_by}</td>
                    <td className="settings-table-reason">{entry.change_reason}</td>
                    <td>{summariseItems(entry.after).join("、") || "-"}</td>
                    <td>{formatTime(entry.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "keys" && (
        <section className="settings-card settings-card-wide" aria-label="密钥发放记录">
          <div className="settings-card-head">
            <div>
              <h3>批次密钥发放记录</h3>
              <p className="settings-card-description">
                密钥随冻结批次下发、6 小时时效作废；此处仅展示密钥标识，不显示密钥明文。
              </p>
            </div>
          </div>
          {grants.length === 0 ? (
            <p className="settings-empty">暂无密钥发放记录。</p>
          ) : (
            <table className="settings-table">
              <thead>
                <tr>
                  <th>账号</th>
                  <th>工作区</th>
                  <th>冻结批次</th>
                  <th>提供方</th>
                  <th>密钥标识</th>
                  <th>发放时间</th>
                  <th>过期时间</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => (
                  <tr key={grant.grant_id}>
                    <td>{grant.account_id}</td>
                    <td>{grant.workspace_id}</td>
                    <td>{grant.freeze_id}</td>
                    <td>{grant.provider}</td>
                    <td>{grant.key_label}</td>
                    <td>{formatTime(grant.granted_at)}</td>
                    <td>{formatTime(grant.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {editOpen && (
        <div className="settings-dialog-backdrop" role="presentation" onMouseDown={() => !busy && setEditOpen(false)}>
          <section
            className="settings-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pricing-edit-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h2 id="pricing-edit-title">编辑子项定价</h2>
            <p className="settings-card-description">
              当前规则版本 v{ruleVersion}；保存后将生成 v{ruleVersion + 1} 并写入审计日志，在途批次仍按冻结时版本结算。
            </p>
            <div className="settings-edit-grid">
              {FEATURE_KEYS.map((key) => (
                <label key={key} className="settings-field">
                  <span>{SUBITEM_LABELS[key] ?? key}</span>
                  <input
                    type="number"
                    min={0}
                    max={45}
                    step={0.5}
                    value={Number.isFinite(draftItems[key]) ? draftItems[key] : ""}
                    onChange={(event) => setDraftItems((current) => ({ ...current, [key]: Number(event.target.value) }))}
                    disabled={busy}
                  />
                </label>
              ))}
            </div>
            <p className={`settings-edit-total${draftTotal < 35 || draftTotal > 45 ? " is-invalid" : ""}`}>
              合计：{draftTotal} 积分（须在 35~45 之间）
            </p>
            <label className="settings-field">
              <span>变更原因（必填）</span>
              <input
                type="text"
                maxLength={500}
                placeholder="例如：图片上游涨价，智能生图单价上调 2 积分"
                value={changeReason}
                onChange={(event) => setChangeReason(event.target.value)}
                disabled={busy}
              />
            </label>
            {error && <p className="settings-status is-error" role="alert">{error}</p>}
            <div className="settings-actions">
              <div />
              <div className="settings-action-buttons">
                <button type="button" className="settings-secondary-button" onClick={() => setEditOpen(false)} disabled={busy}>
                  取消
                </button>
                <button type="button" className="primary-button" onClick={() => void savePricing()} disabled={busy}>
                  {busy ? "保存中…" : "保存新版本"}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {podEditOpen && (
        <div className="settings-dialog-backdrop" role="presentation" onMouseDown={() => !busy && setPodEditOpen(false)}>
          <section
            className="settings-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pod-pricing-edit-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h2 id="pod-pricing-edit-title">编辑 POD AI 调用单价</h2>
            <p className="settings-card-description">
              当前规则版本 v{podRuleVersion}；保存后生成新版本，在途任务仍按冻结版本结算。
            </p>
            <div className="settings-edit-grid">
              {POD_FEATURE_KEYS.map((key) => (
                <label key={key} className="settings-field">
                  <span>{POD_FEATURE_LABELS[key]}</span>
                  <input
                    type="number"
                    min={0}
                    max={10000}
                    step={0.1}
                    value={Number.isFinite(podDraftItems[key]) ? podDraftItems[key] : ""}
                    onChange={(event) => setPodDraftItems((current) => ({ ...current, [key]: Number(event.target.value) }))}
                    disabled={busy}
                  />
                </label>
              ))}
            </div>
            <label className="settings-field">
              <span>变更原因（必填）</span>
              <input
                type="text"
                maxLength={500}
                placeholder="例如：POD 图片供应商成本调整"
                value={podChangeReason}
                onChange={(event) => setPodChangeReason(event.target.value)}
                disabled={busy}
              />
            </label>
            {error && <p className="settings-status is-error" role="alert">{error}</p>}
            <div className="settings-actions">
              <div />
              <div className="settings-action-buttons">
                <button type="button" className="settings-secondary-button" onClick={() => setPodEditOpen(false)} disabled={busy}>取消</button>
                <button type="button" className="primary-button" onClick={() => void savePodPricing()} disabled={busy}>
                  {busy ? "保存中…" : "保存 POD 新版本"}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
