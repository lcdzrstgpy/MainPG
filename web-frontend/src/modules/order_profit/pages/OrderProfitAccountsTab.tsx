import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import type {
  OrderProfitCaptureStatus,
  StoreAccount,
  TeamLinkItem,
  TeamLinksPayload,
} from "../types";

const REGIONS: Array<{ value: string; label: string }> = [
  { value: "global", label: "全球" },
  { value: "us", label: "美国" },
  { value: "eu", label: "欧区" },
  { value: "cn", label: "中国" },
];

const STATUS_LABEL: Record<string, string> = {
  idle: "未登录",
  logging_in: "登录中",
  capturing: "采集中",
  ready: "已就绪",
  error: "登录失败",
};

type Form = {
  id: string;
  alias: string;
  phone: string;
  password: string;
  region: string;
  shopName: string;
};

function emptyForm(): Form {
  return { id: "", alias: "", phone: "", password: "", region: "global", shopName: "" };
}

function linkInitial(name: string): string {
  const text = String(name || "").trim();
  return text ? text.slice(0, 1).toUpperCase() : "?";
}

/** 卡片右下角的波浪装饰：多层波形，靠遮罩向边缘淡出。 */
function EntryWaves() {
  return (
    <svg className="op-entry-waves" viewBox="0 0 320 200" preserveAspectRatio="none" aria-hidden="true">
      <path
        d="M0 112 C 34 82 66 82 100 112 S 166 142 200 112 S 266 82 320 112 V200 H0 Z"
        fill="currentColor"
        opacity=".1"
      />
      <path
        d="M0 134 C 34 104 66 104 100 134 S 166 164 200 134 S 266 104 320 134 V200 H0 Z"
        fill="currentColor"
        opacity=".09"
      />
      <path
        d="M0 156 C 34 126 66 126 100 156 S 166 186 200 156 S 266 126 320 156 V200 H0 Z"
        fill="currentColor"
        opacity=".08"
      />
    </svg>
  );
}

function StoreIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3.2 9.5 5 4.5h14l1.8 5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4.2 9.5h15.6V19a1.2 1.2 0 0 1-1.2 1.2H5.4A1.2 1.2 0 0 1 4.2 19V9.5Z" strokeLinejoin="round" />
      <path d="M9.6 20.2v-5.4h4.8v5.4" strokeLinejoin="round" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.6 19.2c0-3 2.4-5.2 5.4-5.2s5.4 2.2 5.4 5.2" strokeLinecap="round" />
      <circle cx="17.2" cy="9.2" r="2.4" />
      <path d="M16.2 14.4c2.4.2 4.2 2 4.2 4.4" strokeLinecap="round" />
    </svg>
  );
}

function GoArrow() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M5 12h12" strokeLinecap="round" />
      <path d="m12 6 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function OrderProfitAccountsTab({ isActive }: { isActive: boolean }) {
  const [accounts, setAccounts] = useState<StoreAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState<Form>(emptyForm);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState("");
  const [view, setView] = useState<"home" | "accounts" | "links">("home");
  const [jobs, setJobs] = useState<Record<string, OrderProfitCaptureStatus>>({});
  const [links, setLinks] = useState<TeamLinksPayload | null>(null);
  const [linkTarget, setLinkTarget] = useState("");
  const [linkBusy, setLinkBusy] = useState("");
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await orderProfitApi.accounts();
      setAccounts(payload.accounts || []);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLinks = useCallback(async () => {
    try {
      setLinks(await orderProfitApi.teamLinks());
    } catch (cause) {
      setLinks({
        available: false,
        reason: cause instanceof Error ? cause.message : String(cause),
      });
    }
  }, []);

  useEffect(() => {
    if (!isActive) return;
    void load();
    void loadLinks();
  }, [isActive, load, loadLinks]);

  // 登录中/采集中的账号：轮询指令状态，显示进度与卡点提示。
  useEffect(() => {
    if (!isActive) return;
    const pending = accounts.filter(
      (account) =>
        (account.status === "logging_in" || account.status === "capturing")
        && account.last_command_id > 0,
    );
    if (!pending.length) {
      setJobs({});
      return;
    }
    let cancelled = false;
    const tick = async () => {
      for (const account of pending) {
        if (cancelled) return;
        try {
          const status = await orderProfitApi.captureStatus(account.last_command_id);
          if (cancelled) return;
          setJobs((current) => ({ ...current, [account.id]: status }));
          if (["succeeded", "failed", "cancelled"].includes(status.status)) {
            if (status.command_type === "temu_capture_all") {
              const label = account.alias || account.shop_name || `尾号 ${account.phone_tail}`;
              if (status.status === "succeeded" && status.ok) {
                showToast(`「${label}」采集完成：${status.status_text || "数据已入库"}`, "success");
              } else {
                showToast(status.status_text || status.error || "采集失败", status.status === "cancelled" ? "info" : "error");
              }
            }
            void load();
          }
        } catch (_error) {
          // 指令还没落库时忽略。
        }
      }
    };
    void tick();
    timer.current = window.setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [accounts, isActive, load]);

  const submitForm = async () => {
    if (!form.phone.trim()) {
      showToast("请填写手机号", "error");
      return;
    }
    if (!form.id && !form.password.trim()) {
      showToast("自动填表需要密码：新增账号请填写登录密码", "error");
      return;
    }
    setBusy("form");
    try {
      await orderProfitApi.saveAccount({
        id: form.id || undefined,
        alias: form.alias,
        phone: form.phone,
        password: form.password || undefined,
        region: form.region,
        shop_name: form.shopName.trim(),
      });
      showToast(form.id ? "账号已更新" : "账号已保存（密码已本机加密）", "success");
      setForm(emptyForm());
      setShowForm(false);
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const startEdit = (account: StoreAccount) => {
    setForm({
      id: account.id,
      alias: account.alias,
      phone: account.phone,
      password: "",
      region: account.region || "global",
      shopName: account.shop_name || "",
    });
    setShowForm(true);
  };

  const openBrowser = async (account: StoreAccount) => {
    setBusy(account.id);
    try {
      const result = await orderProfitApi.openAccountBrowser(account.id);
      showToast(`已启动独立浏览器（${result.browser}），等插件连上后即可登录采集`, "success");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const closeBrowser = async (account: StoreAccount) => {
    setBusy(account.id);
    try {
      await orderProfitApi.closeAccountBrowser(account.id);
      showToast("已关闭该账号的浏览器", "info");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  /** 只登录并绑定店铺（不采集）。 */
  const startLogin = async (account: StoreAccount) => {
    setBusy(account.id);
    try {
      // 还没为该账号打开独立浏览器：先自动打开，再让用户在窗口里连一次插件。
      if (!account.instance_pid) {
        const opened = await orderProfitApi.openAccountBrowser(account.id);
        showToast(
          `已打开独立浏览器（${opened.browser}）：请在该窗口登录工作台并点一次「连接插件」，再回来点「登录并绑定」`,
          "info",
        );
        await load();
        return;
      }
      const command = await orderProfitApi.startAccountLogin(account.id, false, false);
      showToast(
        "已下发绑定任务：插件会自动填账号密码，验证码需你在浏览器里输入；登录后自动读取并绑定店铺",
        "info",
      );
      setJobs((current) => ({ ...current, [account.id]: { command_id: command.command_id } as OrderProfitCaptureStatus }));
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  /** 清除该店铺已采集的数据，保留店铺和账号绑定。 */
  const clearData = async (account: StoreAccount) => {
    const shopName = account.shop_name || "";
    if (!shopName) return;
    const confirmed = window.confirm(
      `清除店铺「${shopName}」的采集数据？\n订单、流水、流量、销量、结算都会被删除，店铺和账号绑定保留。`,
    );
    if (!confirmed) return;
    setBusy(account.id);
    try {
      await orderProfitApi.clearAccountData(account.id);
      showToast(`已清除「${shopName}」的采集数据（绑定保留）`, "success");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const remove = async (account: StoreAccount) => {
    if (!window.confirm(`删除账号「${account.alias || account.phone_tail}」？本机保存的密码也会一起删除。`)) return;
    setBusy(account.id);
    try {
      await orderProfitApi.deleteAccount(account.id);
      showToast("账号已删除", "success");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  const requestLink = async () => {
    const target = linkTarget.trim();
    if (!target) {
      showToast("请填写对方账号 ID", "error");
      return;
    }
    setLinkBusy("request");
    try {
      setLinks(await orderProfitApi.requestTeamLink(target));
      setLinkTarget("");
      showToast("已发送关联申请，等待对方同意", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLinkBusy("");
    }
  };

  const acceptLink = async (item: TeamLinkItem) => {
    setLinkBusy(item.account_id);
    try {
      setLinks(await orderProfitApi.acceptTeamLink(item.account_id));
      showToast(`已同意 ${item.username || item.account_id} 的关联申请`, "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLinkBusy("");
    }
  };

  const rejectLink = async (item: TeamLinkItem) => {
    setLinkBusy(item.account_id);
    try {
      setLinks(await orderProfitApi.rejectTeamLink(item.account_id));
      showToast("已拒绝该关联申请", "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLinkBusy("");
    }
  };

  const removeLink = async (item: TeamLinkItem) => {
    const pending = item.status === "pending";
    if (!window.confirm(`${pending ? "撤回" : "移除"}与「${item.username || item.account_id}」的关联？`)) return;
    setLinkBusy(item.account_id);
    try {
      setLinks(await orderProfitApi.removeTeamLink(item.account_id));
      showToast(pending ? "已撤回申请" : "已移除关联", "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLinkBusy("");
    }
  };

  const linkReason = (() => {
    const reason = links?.reason || "";
    if (reason === "not_logged_in") return "请先登录工作台账号，登录后即可关联其他账号";
    if (reason === "团队服务未配置") return "团队服务未配置，暂不能关联账号";
    return reason;
  })();
  const myName = links?.username || links?.staff_no || "";
  const pendingIn = links?.pending_in || [];
  const outRows = [...(links?.linked || []), ...(links?.pending_out || [])];
  const linkers = links?.linked_by || [];

  return (
    <section className="op-view">
      {view === "home" ? (
        <div className="op-entry-grid">
          <button type="button" className="op-entry" onClick={() => setView("accounts")}>
            <EntryWaves />
            <span className="op-entry-icon">
              <StoreIcon />
            </span>
            <span className="op-entry-body">
              <strong>店铺账号</strong>
              <small>管理店铺登录账号，独立浏览器登录与采集</small>
            </span>
            <span className="op-entry-foot">
              <span className="op-chip">{accounts.length} 个账号</span>
              <span className="op-entry-go">
                进入
                <GoArrow />
              </span>
            </span>
          </button>

          <button type="button" className="op-entry is-links" onClick={() => setView("links")}>
            <EntryWaves />
            <span className="op-entry-icon">
              <LinkIcon />
            </span>
            <span className="op-entry-body">
              <strong>账号关联</strong>
              <small>与同事账号互相授权，在团队看板共享店铺数据</small>
            </span>
            <span className="op-entry-foot">
              {pendingIn.length ? (
                <span className="op-chip is-warn">待同意 {pendingIn.length}</span>
              ) : null}
              <span className="op-chip">
                {links?.available ? `名额 ${links.linked_count ?? 0}/${links.max_links ?? 3}` : "未登录"}
              </span>
              <span className="op-entry-go">
                进入
                <GoArrow />
              </span>
            </span>
          </button>
        </div>
      ) : null}

      {view === "accounts" ? (
        <>
          <div className="op-card op-toolbar">
            <div className="op-toolbar-main">
              <button type="button" className="op-btn op-btn-sm" onClick={() => setView("home")}>
                返回
              </button>
              <strong>店铺账号</strong>
            </div>
            <button
              type="button"
              className="op-btn op-btn-primary"
              onClick={() => {
                setForm(emptyForm());
                setShowForm((value) => !value);
              }}
            >
              {showForm ? "取消新增" : "新增店铺账号"}
            </button>
          </div>

      {showForm ? (
        <div className="op-card op-panel">
          <div className="op-panel-head">
            <strong>{form.id ? "编辑账号" : "新增店铺账号"}</strong>
            <span className="op-chip">密码本机加密</span>
          </div>
          <div className="op-settings-grid">
            <label className="op-field">
              <span>手机号</span>
              <input
                type="tel"
                value={form.phone}
                placeholder="Temu 卖家账号手机号"
                onChange={(event) => setForm({ ...form, phone: event.target.value })}
              />
            </label>
            <label className="op-field">
              <span>密码</span>
              <input
                type="password"
                value={form.password}
                placeholder="登录密码，插件自动填入"
                onChange={(event) => setForm({ ...form, password: event.target.value })}
              />
            </label>
            <label className="op-field">
              <span>备注名</span>
              <input
                type="text"
                value={form.alias}
                placeholder="例如：美国站主店"
                onChange={(event) => setForm({ ...form, alias: event.target.value })}
              />
            </label>
            <label className="op-field">
              <span>店铺名</span>
              <input
                type="text"
                value={form.shopName}
                readOnly={Boolean(form.shopName)}
                placeholder="留空自动读取"
                onChange={(event) => setForm({ ...form, shopName: event.target.value })}
              />
            </label>
            <label className="op-field">
              <span>默认区域</span>
              <select value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })}>
                {REGIONS.map((region) => (
                  <option key={region.value} value={region.value}>{region.label}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" disabled={busy === "form"} onClick={() => void submitForm()}>
              {busy === "form" ? "保存中…" : "保存账号"}
            </button>
            <button type="button" className="op-btn" onClick={() => setShowForm(false)}>取消</button>
          </div>
        </div>
      ) : null}

      {loading && !accounts.length ? (
        <div className="op-stats-grid">
          {Array.from({ length: 3 }).map((_, index) => (
            <div className="op-skeleton op-skeleton-stat" key={index} />
          ))}
        </div>
      ) : null}

      {!loading && !accounts.length ? (
        <div className="op-card">
          <div className="op-empty">
            <strong>还没有店铺账号</strong>
            <span>点右上角「新增店铺账号」录入手机号和密码。</span>
          </div>
        </div>
      ) : null}

      <div className="op-account-list">
        {accounts.map((account) => {
          const job = jobs[account.id];
          const percent = job?.progress?.percent ?? 0;
          return (
            <div className="op-card op-account" key={account.id}>
              <div className="op-account-head">
                <div className="op-account-title">
                  <strong>{account.alias || `尾号 ${account.phone_tail}`}</strong>
                  <span className="op-chip">{REGIONS.find((item) => item.value === account.region)?.label || account.region}</span>
                  <span className={`op-chip ${account.status === "ready" ? "is-ok" : account.status === "error" ? "is-error" : account.status === "logging_in" || account.status === "capturing" ? "is-warn" : ""}`}>
                    {STATUS_LABEL[account.status] || account.status}
                  </span>
                </div>
                <div className="op-account-actions">
                  <button
                    type="button"
                    className="op-btn"
                    disabled={busy === account.id}
                    onClick={() => void (account.instance_pid ? closeBrowser(account) : openBrowser(account))}
                  >
                    {account.instance_pid ? "关闭浏览器" : "打开浏览器"}
                  </button>
                  <button
                    type="button"
                    className="op-btn"
                    disabled={busy === account.id || !account.has_password}
                    title={account.has_password ? "" : "请先编辑账号补上密码"}
                    onClick={() => void startLogin(account)}
                  >
                    登录并绑定
                  </button>
                  <button type="button" className="op-btn" disabled={busy === account.id} onClick={() => startEdit(account)}>
                    编辑
                  </button>
                  <button
                    type="button"
                    className="op-btn"
                    disabled={busy === account.id || !account.shop_name}
                    onClick={() => void clearData(account)}
                  >
                    清除数据
                  </button>
                  <button type="button" className="op-btn" disabled={busy === account.id} onClick={() => void remove(account)}>
                    删除
                  </button>
                </div>
              </div>
              <div className="op-account-meta">
                <span>手机号：{account.phone || `尾号 ${account.phone_tail}`}</span>
                <span>
                  店铺：
                  {account.shop_name ? (
                    <b className="is-positive">{account.shop_name}</b>
                  ) : (
                    <b className="op-account-unbound">未绑定（登录后自动获取）</b>
                  )}
                </span>
                <span>最近登录：{account.last_login_at || "—"}</span>
                {account.last_error ? <span className="is-negative">错误：{account.last_error}</span> : null}
              </div>
              {job ? (
                <div className="op-account-progress">
                  <div className="op-progress-head">
                    <span>{job.progress?.step_label || "登录中"}</span>
                    <strong>{percent}%</strong>
                  </div>
                  <div className="op-progress-track">
                    <div className="op-progress-bar" style={{ width: `${percent}%` }} />
                  </div>
                  {job.status_text ? <small>{job.status_text}</small> : null}
                  {String(job.waiting_for || "").startsWith("manual") ? (
                    <small className="op-account-manual">
                      请切到对应浏览器完成「{job.progress?.step_label || "当前步骤"}」，完成后会自动继续
                    </small>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
          </div>
        </>
      ) : null}

      {view === "links" ? (
        <>
          <div className="op-card op-toolbar">
            <div className="op-toolbar-main">
              <button type="button" className="op-btn op-btn-sm" onClick={() => setView("home")}>
                返回
              </button>
              <strong>账号关联</strong>
            </div>
          </div>

      <div className="op-card op-panel op-links">
        <div className="op-panel-head op-links-head">
          <span className="op-links-head-meta">
            {pendingIn.length ? (
              <span className="op-chip is-warn">待我同意 {pendingIn.length}</span>
            ) : null}
            <span className="op-chip">
              {links?.available ? `名额 ${links.linked_count ?? 0}/${links.max_links ?? 3}` : "未启用"}
            </span>
          </span>
        </div>
        {links?.available ? (
          <>
            <div className="op-links-top">
              <div className="op-links-me">
                <span className="op-links-me-avatar">{linkInitial(myName || links.account_id || "")}</span>
                <div className="op-links-me-body">
                  <div className="op-links-me-name">
                    {myName || "未登记员工标识"}
                    {links.username && links.staff_no ? <span className="op-chip">{links.staff_no}</span> : null}
                  </div>
                  <div className="op-links-me-id">
                    <span>{links.account_id || "—"}</span>
                    <button
                      type="button"
                      className="op-links-copy"
                      disabled={!links.account_id}
                      onClick={() => {
                        if (!links.account_id) return;
                        void navigator.clipboard.writeText(links.account_id);
                        showToast("已复制账号 ID", "success");
                      }}
                    >
                      复制
                    </button>
                  </div>
                </div>
              </div>
              <div className="op-links-request">
                <input
                  type="text"
                  value={linkTarget}
                  placeholder="对方账号 ID / 登录账号 / 员工标识"
                  onChange={(event) => setLinkTarget(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void requestLink();
                  }}
                />
                <button
                  type="button"
                  className="op-btn op-btn-primary"
                  disabled={linkBusy === "request" || (links.linked_count ?? 0) >= (links.max_links ?? 3)}
                  onClick={() => void requestLink()}
                >
                  发送申请
                </button>
              </div>
            </div>

            {pendingIn.length ? (
              <div className="op-links-block">
                <div className="op-links-block-head">
                  <strong>待我同意</strong>
                  <small>{pendingIn.length} 条申请</small>
                </div>
                <div className="op-link-list">
                  {pendingIn.map((item) => (
                    <div className="op-link-row" key={item.account_id}>
                      <span className="op-link-avatar is-warn">{linkInitial(item.username || item.account_id)}</span>
                      <div className="op-link-main">
                        <div className="op-link-name">{item.username || "未登记登录账号"}</div>
                        <div className="op-link-sub">
                          <span>{item.account_id}</span>
                          {item.staff_no ? <span>员工标识 {item.staff_no}</span> : null}
                          <span>{item.store_count || 0} 家店铺</span>
                        </div>
                      </div>
                      <div className="op-link-actions">
                        <button
                          type="button"
                          className="op-btn op-btn-primary op-btn-sm"
                          disabled={linkBusy === item.account_id}
                          onClick={() => void acceptLink(item)}
                        >
                          同意
                        </button>
                        <button
                          type="button"
                          className="op-btn op-btn-sm"
                          disabled={linkBusy === item.account_id}
                          onClick={() => void rejectLink(item)}
                        >
                          拒绝
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="op-links-block">
              <div className="op-links-block-head">
                <strong>我关联的账号</strong>
                <small>
                  已关联 {links.accepted_count ?? 0} · 待同意 {links.pending_out_count ?? 0}
                </small>
              </div>
              {outRows.length ? (
                <div className="op-link-list">
                  {outRows.map((item) => (
                    <div className="op-link-row" key={item.account_id}>
                      <span className="op-link-avatar">{linkInitial(item.username || item.account_id)}</span>
                      <div className="op-link-main">
                        <div className="op-link-name">
                          {item.username || "未登记登录账号"}
                          <span className={`op-chip ${item.status === "pending" ? "is-warn" : "is-ok"}`}>
                            {item.status === "pending" ? "待对方同意" : "已关联"}
                          </span>
                        </div>
                        <div className="op-link-sub">
                          {item.staff_no ? <span>员工标识 {item.staff_no}</span> : null}
                          <span>{item.store_count || 0} 家店铺</span>
                        </div>
                      </div>
                      <div className="op-link-actions">
                        <button
                          type="button"
                          className="op-btn op-btn-sm"
                          disabled={linkBusy === item.account_id}
                          onClick={() => void removeLink(item)}
                        >
                          {item.status === "pending" ? "撤回" : "移除"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="op-links-empty">还没有关联账号，在上方输入对方账号发送申请</div>
              )}
            </div>

            <div className="op-links-block">
              <div className="op-links-block-head">
                <strong>关联我的账号</strong>
                <small>{links.linked_by_count ?? 0} 个账号</small>
              </div>
              {linkers.length ? (
                <div className="op-link-chips">
                  {linkers.map((item) => (
                    <span className="op-link-chip" key={item.account_id}>
                      <span className="op-link-avatar is-sm">{linkInitial(item.username || item.account_id)}</span>
                      <span>{item.username || item.account_id}</span>
                      {item.staff_no ? <small>{item.staff_no}</small> : null}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="op-links-empty">还没有人关联你</div>
              )}
            </div>
          </>
        ) : (
          <div className="op-empty op-empty-compact">
            <strong>关联账号不可用</strong>
            <span>{linkReason || "请确认已登录工作台账号"}</span>
          </div>
        )}
      </div>
        </>
      ) : null}
    </section>
  );
}
