import { useCallback, useEffect, useState } from "react";

import {
  loadMyStationApplication,
  submitStationApplication,
  type StationApplicationState,
} from "../api/personalCenterApi";

type PromoTier = {
  /** 档位代号，如 V2 / ×1.5 */
  tier: string;
  /** 触发该档位的区间 */
  range: string;
  /** 该档位对应的返点基点或倍率 */
  value: string;
};

/** 返点基点阶梯：署名（归属）用户数越多，基础返点率越高。 */
const USER_TIERS: PromoTier[] = [
  { tier: "V0", range: "0 – 3 人", value: "3.0%" },
  { tier: "V1", range: "4 – 10 人", value: "4.5%" },
  { tier: "V2", range: "11 – 15 人", value: "6.0%" },
  { tier: "V3", range: "16 – 20 人", value: "8.0%" },
  { tier: "V4", range: "21 人及以上", value: "10.0%" },
];

/** 返点倍率阶梯：署名用户累计充值金额越高，倍率越高。 */
const TOPUP_TIERS: PromoTier[] = [
  { tier: "T1", range: "100 – 299 元", value: "×1.0" },
  { tier: "T2", range: "300 – 499 元", value: "×1.2" },
  { tier: "T3", range: "500 – 999 元", value: "×1.5" },
  { tier: "T4", range: "1,000 – 1,999 元", value: "×1.8" },
  { tier: "T5", range: "2,000 元及以上", value: "×2.0" },
];

const PROMO_NOTES: { icon: string; label: string; text: string }[] = [
  { icon: "icon-user", label: "参与对象", text: "所有已注册用户均可申请加入；成立分站需提交申请，1~3 个工作日内答复。" },
  { icon: "icon-link", label: "署名归属", text: "用户首次通过你的署名入口注册，即终身归属到你的名下。" },
  { icon: "icon-calendar", label: "结算周期", text: "每自然月结算一次，次月 1 日生成上一周期返点账单。" },
  { icon: "icon-moneycollect", label: "发放方式", text: "返点以积分形式发放至积分钱包，可直接用于消费。" },
];

const PROMO_FORMS: { step: string; title: string; text: string }[] = [
  { step: "01", title: "署名推广", text: "以署名身份对外推广，用户注册时自动带上你的署名标记，形成长期归属关系。" },
  { step: "02", title: "站点署名", text: "支持以站点形式署名，你站点下的用户统一计入你的署名用户数，规模越大档位越高。" },
  { step: "03", title: "订单归属", text: "署名用户的每一笔充值都会按署名关系自动归属到你的账户，无需人工对账。" },
  { step: "04", title: "阶梯返点", text: "按「署名用户数定基点、充值金额定倍率」两维叠加计算，达到档位即时生效。" },
];

/** 分站申请状态的展示口径：标签、提示语。 */
const STATION_STATUS_META: Record<
  StationApplicationState["status"],
  { label: string; hint: string }
> = {
  pending: {
    label: "审核中",
    hint: "申请已提交，我们会在 1~3 个工作日内答复，结果以站内公告形式通知。",
  },
  approved: {
    label: "已成立分站",
    hint: "分站专属账号密码已通过站内公告定向发送，请到公告/消息中心查收后登录。",
  },
  rejected: {
    label: "未通过",
    hint: "本次申请未通过，可补充资料后重新提交。",
  },
  revoked: {
    label: "已撤销",
    hint: "分站资格已撤销，如需继续运营，可重新提交申请。",
  },
};

function formatApplyTime(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function PromoTierTable({
  title,
  caption,
  unit,
  tiers,
}: {
  title: string;
  caption: string;
  unit: string;
  tiers: PromoTier[];
}) {
  return (
    <section className="promo-tier">
      <header className="promo-tier-head">
        <h3>{title}</h3>
        <small>{caption}</small>
      </header>
      <ul className="promo-tier-list">
        {tiers.map((item, index) => (
          <li key={item.tier} className={index === tiers.length - 1 ? "is-top" : ""}>
            <b className="promo-tier-code">{item.tier}</b>
            <span className="promo-tier-range">{item.range}</span>
            <strong className="promo-tier-value">{item.value}</strong>
            <em>{unit}</em>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PromotionPlanPanel() {
  const [application, setApplication] = useState<StationApplicationState | null>(null);
  const [applyLoading, setApplyLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState("");
  const [applyNotice, setApplyNotice] = useState("");

  const loadApplication = useCallback(async () => {
    setApplyLoading(true);
    setApplyError("");
    try {
      const data = await loadMyStationApplication();
      setApplication(data.application ?? null);
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "申请状态加载失败");
    } finally {
      setApplyLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadApplication();
  }, [loadApplication]);

  const handleApply = useCallback(async () => {
    const confirmed = window.confirm(
      "确定提交成立分站申请吗？\n提交后我们会在 1~3 个工作日内答复，结果会以站内公告的形式通知你。",
    );
    if (!confirmed) return;
    setApplying(true);
    setApplyError("");
    setApplyNotice("");
    try {
      const data = await submitStationApplication({});
      setApplication(data.application ?? null);
      setApplyNotice(data.message || "申请已提交，我们会在 1~3 个工作日内答复。");
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "申请提交失败，请稍后重试");
    } finally {
      setApplying(false);
    }
  }, []);

  const canApply = !application || application.status === "rejected" || application.status === "revoked";
  const statusMeta = application ? STATION_STATUS_META[application.status] : null;

  return (
    <article className="personal-card promo-card">
      <div className="personal-card-title">
        <span className="iconfont icon-gift" aria-hidden="true" />
        <div>
          <h2>推广计划</h2>
          <small>署名用户越多，返点基点越高；充值金额越多，返点倍率越高。</small>
        </div>
      </div>

      <section className="promo-hero">
        <span className="promo-hero-kicker">推广返点 · 双维阶梯激励</span>
        <div className="promo-hero-formula">
          <b>返点基点</b>
          <em>×</em>
          <b>返点倍率</b>
          <i>=</i>
          <strong>实际返点率</strong>
        </div>
        <p className="promo-hero-note">
          返点率上限 20%，具体档位以当期账单为准；实际返点以署名用户当期实付充值为基数计算。
        </p>
      </section>

      <section className="promo-apply">
        <header className="promo-section-head">
          <h3>申请成立分站</h3>
          <small>审核通过后开通中转网址的专属账号密码，独立运营你的分站</small>
        </header>

        {applyLoading ? (
          <p className="promo-apply-hint">正在读取申请状态…</p>
        ) : application ? (
          <div className={`promo-apply-state is-${application.status}`}>
            <div className="promo-apply-state-head">
              <strong className="promo-apply-badge">{statusMeta?.label}</strong>
              <span className="promo-apply-time">申请时间 {formatApplyTime(application.applied_at)}</span>
            </div>
            <p className="promo-apply-hint">{statusMeta?.hint}</p>
            {application.status === "approved" ? (
              <dl className="promo-apply-kv">
                <div>
                  <dt>分站账号</dt>
                  <dd>{application.station_username || "—"}</dd>
                </div>
                <div>
                  <dt>登录地址</dt>
                  <dd>
                    {application.login_url ? (
                      <a href={application.login_url} target="_blank" rel="noreferrer">
                        {application.login_url}
                      </a>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              </dl>
            ) : null}
            {application.status === "rejected" && application.reject_reason ? (
              <p className="promo-apply-reason">驳回理由：{application.reject_reason}</p>
            ) : null}
            {canApply ? (
              <button
                type="button"
                className="personal-plan-claim-btn promo-apply-btn"
                onClick={handleApply}
                disabled={applying}
              >
                {applying ? "提交中…" : "重新申请"}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="promo-apply-empty">
            <p className="promo-apply-hint">
              提交申请后我们会在 1~3 个工作日内答复，批准后以站内公告定向发送分站专属账号密码。
            </p>
            <button
              type="button"
              className="personal-plan-claim-btn promo-apply-btn"
              onClick={handleApply}
              disabled={applying}
            >
              {applying ? "提交中…" : "申请加入"}
            </button>
          </div>
        )}

        {applyNotice ? <p className="promo-apply-notice">{applyNotice}</p> : null}
        {applyError ? <p className="promo-apply-error">{applyError}</p> : null}
      </section>

      <section className="promo-section">
        <header className="promo-section-head">
          <h3>计划说明</h3>
          <small>推广计划的参与方式与结算口径</small>
        </header>
        <div className="promo-note-grid">
          {PROMO_NOTES.map((note) => (
            <div key={note.label} className="promo-note">
              <span className={`iconfont ${note.icon}`} aria-hidden="true" />
              <div>
                <b>{note.label}</b>
                <p>{note.text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="promo-section">
        <header className="promo-section-head">
          <h3>推广形式</h3>
          <small>从署名到返点结算的完整链路</small>
        </header>
        <div className="promo-form-grid">
          {PROMO_FORMS.map((form) => (
            <div key={form.step} className="promo-form">
              <span className="promo-form-step">{form.step}</span>
              <b>{form.title}</b>
              <p>{form.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="promo-section">
        <header className="promo-section-head">
          <h3>返点规则</h3>
          <small>两个维度分别取档，同周期内叠加计算</small>
        </header>
        <div className="promo-tier-grid">
          <PromoTierTable
            title="署名用户数 → 返点基点"
            caption="你的站点署名用户越多，基础返点越高"
            unit="基点"
            tiers={USER_TIERS}
          />
          <PromoTierTable
            title="充值金额 → 返点倍率"
            caption="署名用户累计充值越多，倍率越高"
            unit="倍率"
            tiers={TOPUP_TIERS}
          />
        </div>
      </section>

      <section className="promo-example">
        <span className="promo-example-kicker">返点试算示例</span>
        <p className="promo-example-formula">返点金额 = 署名用户当期实付充值 × 返点基点 × 返点倍率</p>
        <p className="promo-example-body">
          你的署名用户为 <b>60</b> 人（V4 档 → 返点基点 <b>10.0%</b>），他们本周期累计充值 <b>6,000</b> 元
          （T5 档 → 返点倍率 <b>×2.0</b>），则本周期返点 = 6,000 × 10.0% × 2.0 = <strong>1,200 元</strong>。
        </p>
      </section>
    </article>
  );
}
