import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { ShopDataArt } from "../components/ShopDataArt";
import { ShopDataScene } from "../components/ShopDataScene";
import { EmptyBlock } from "../components/ShopDataPrimitives";
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

export function OrderProfitAccountsTab({ isActive, onOpenCapture }: { isActive: boolean; onOpenCapture?: () => void }) {
  const [accounts, setAccounts] = useState<StoreAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [form, setForm] = useState<Form>(emptyForm);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState("");
  const [waitingAccountId, setWaitingAccountId] = useState("");
  const [view, setView] = useState<"home" | "accounts" | "links">("home");
  const [jobs, setJobs] = useState<Record<string, OrderProfitCaptureStatus>>({});
  const [links, setLinks] = useState<TeamLinksPayload | null>(null);
  const [linkTarget, setLinkTarget] = useState("");
  const [linkBusy, setLinkBusy] = useState("");
  const [staffNoDraft, setStaffNoDraft] = useState("");
  const [staffNoBusy, setStaffNoBusy] = useState(false);
  const timer = useRef<number | null>(null);
  // 首次使用（还没有任何账号）直接进入账号列表，不让用户先在两个入口卡里多点一次。
  const autoEnteredAccountsRef = useRef(false);
  // 等待插件连接期间用户可能切走页面，用 ref 及时停掉轮询，避免继续下发登录。
  const isActiveRef = useRef(isActive);
  isActiveRef.current = isActive;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await orderProfitApi.accounts();
      setAccounts(payload.accounts || []);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setLoading(false);
      setLoadedOnce(true);
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

  useEffect(() => {
    if (autoEnteredAccountsRef.current || !loadedOnce) return;
    if (!accounts.length && view === "home") {
      autoEnteredAccountsRef.current = true;
      setView("accounts");
    }
  }, [accounts.length, loadedOnce, view]);

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

  /** 当前账号的独立浏览器里是否已经有在线插件会话。 */
  const hasInstanceSession = async (instanceTag: string): Promise<boolean> => {
    const tag = String(instanceTag || "").trim();
    if (!tag) return false;
    try {
      const payload = await orderProfitApi.listSessions();
      return (payload.sessions || []).some((session) => (
        session.online
        && String((session.capabilities || {}).instance_tag || "") === tag
      ));
    } catch {
      return false;
    }
  };

  /** 等待账号独立浏览器里的插件连上工作台（最多约 3 分钟）。 */
  const waitForInstanceSession = async (instanceTag: string): Promise<boolean> => {
    const tag = String(instanceTag || "").trim();
    if (!tag) return false;
    for (let attempt = 0; attempt < 90; attempt += 1) {
      if (!isActiveRef.current) return false;
      if (await hasInstanceSession(tag)) return true;
      await new Promise<void>((resolve) => window.setTimeout(resolve, 2000));
    }
    return false;
  };

  const issueLogin = async (account: StoreAccount) => {
    const command = await orderProfitApi.startAccountLogin(account.id, false, false);
    showToast(
      "已下发绑定任务：插件会自动填账号密码；验证码请在新打开的浏览器里输入，登录后自动绑定店铺",
      "info",
    );
    setJobs((current) => ({ ...current, [account.id]: { command_id: command.command_id } as OrderProfitCaptureStatus }));
    await load();
  };

  /**
   * 一个按钮完成绑定：
   * 先看独立浏览器里有没有在线插件；没有就真正拉起浏览器，再等插件连上并自动登录。
   * 不再依赖 instance_pid（浏览器关掉后 pid 可能是旧的，会导致“只等待、不打开”）。
   */
  const startLogin = async (account: StoreAccount) => {
    if (busy === account.id || waitingAccountId) return;
    setBusy(account.id);
    try {
      const connectedBefore = await hasInstanceSession(account.instance_tag);
      if (!connectedBefore) {
        const opened = await orderProfitApi.openAccountBrowser(account.id);
        showToast(
          `已打开独立浏览器（${opened.browser}）：请在该窗口登录工作台并点一次「连接插件」，连接后会自动继续`,
          "info",
        );
        await load();
        setWaitingAccountId(account.id);
        const connected = await waitForInstanceSession(account.instance_tag);
        setWaitingAccountId("");
        if (!connected) {
          if (isActiveRef.current) {
            showToast(
              "还没检测到插件连接：请在刚打开的浏览器里登录工作台并点「连接插件」，再点一次「开始绑定」",
              "error",
            );
          }
          return;
        }
      }
      await issueLogin(account);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      if (/还没有连上工作台|plugin session|插件会话|未连接/i.test(message)) {
        // 竞态兜底：再拉一次浏览器，确保界面上真的有一个可操作的窗口。
        try {
          await orderProfitApi.openAccountBrowser(account.id);
        } catch {
          // 打开失败就继续等待已有窗口连接。
        }
        setWaitingAccountId(account.id);
        const connected = await waitForInstanceSession(account.instance_tag);
        setWaitingAccountId("");
        if (connected) {
          try {
            await issueLogin(account);
            return;
          } catch (retryError) {
            showToast(retryError instanceof Error ? retryError.message : String(retryError), "error");
            return;
          }
        }
        if (isActiveRef.current) {
          showToast("浏览器已打开但没有检测到插件连接，请登录工作台后点一次「连接插件」再重试", "error");
        }
        return;
      }
      showToast(message, "error");
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

  /** 清理没有和任何店铺账号绑定的历史店铺（只移除列表项，不删已采数据）。 */
  const cleanupShops = async () => {
    if (!window.confirm("清理所有未与「店铺账号」绑定的店铺？\n只从店铺列表里移除，已采集的数据不会删除。")) {
      return;
    }
    setBusy("cleanup");
    try {
      const result = await orderProfitApi.cleanupShops();
      showToast(
        result.deleted ? `已清理 ${result.deleted} 个未绑定店铺` : "没有需要清理的店铺",
        result.deleted ? "success" : "info",
      );
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy("");
    }
  };

  /** 团队服务首次使用需要员工标识；这里补上，避免到采集时才被动报错。 */
  const saveStaffNo = async () => {
    const value = staffNoDraft.trim();
    if (!value) {
      showToast("请填写员工标识（用于团队汇总辨识）", "error");
      return;
    }
    setStaffNoBusy(true);
    try {
      await orderProfitApi.saveTeamProfile(value);
      showToast("员工标识已保存", "success");
      setStaffNoDraft("");
      await loadLinks();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setStaffNoBusy(false);
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
            <span className="op-entry-head">
              <span className="op-entry-icon">
                <StoreIcon />
              </span>
              <span className="op-entry-scene">
                <ShopDataScene variant="shop" />
              </span>
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
            <span className="op-entry-head">
              <span className="op-entry-icon">
                <LinkIcon />
              </span>
              <span className="op-entry-scene">
                <ShopDataScene variant="link" />
              </span>
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

          <div className="op-card op-panel op-bind-guide">
            <div className="op-panel-head">
              <strong>绑定店铺只需 3 步</strong>
              <span className="op-chip">{accounts.length} 个账号</span>
            </div>
            <div className="op-bind-body">
              <ol className="op-bind-steps">
                <li><b>1</b><span>新增店铺账号：手机号 + 登录密码（本机加密）。</span></li>
                <li><b>2</b><span>点「开始绑定」：在打开的浏览器里登录工作台，并点一次插件「连接插件」。</span></li>
                <li><b>3</b><span>插件自动登录 Temu 并读取店铺；成功后回到「数据采集」同步数据。</span></li>
              </ol>
              <ShopDataArt variant="accounts" className="op-guide-art" seal={false} />
            </div>
            <div className="op-filter-actions">
              <button
                type="button"
                className="op-btn"
                disabled={busy === "cleanup"}
                onClick={() => void cleanupShops()}
              >
                清理未绑定店铺
              </button>
            </div>
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
          <EmptyBlock
            art="accounts"
            title="还没有店铺账号"
            desc="先录入 Temu 店铺账号，之后点「开始绑定」会自动登录并读取店铺。"
            action={
              <button
                type="button"
                className="op-btn op-btn-primary"
                onClick={() => {
                  setForm(emptyForm());
                  setShowForm(true);
                }}
              >
                新增店铺账号
              </button>
            }
          />
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
                    className="op-btn op-btn-primary"
                    disabled={
                      busy === account.id
                      || !account.has_password
                      || account.status === "logging_in"
                      || account.status === "capturing"
                      || Boolean(waitingAccountId)
                    }
                    title={account.has_password ? "" : "请先编辑账号补上密码"}
                    onClick={() => void startLogin(account)}
                  >
                    {account.status === "logging_in"
                      ? "绑定中…"
                      : account.shop_name
                        ? "重新登录"
                        : "开始绑定"}
                  </button>
                  <button
                    type="button"
                    className="op-btn"
                    disabled={busy === account.id || Boolean(waitingAccountId)}
                    onClick={() => void (account.instance_pid ? closeBrowser(account) : openBrowser(account))}
                  >
                    {account.instance_pid ? "关闭浏览器" : "打开浏览器"}
                  </button>
                  {account.shop_name && onOpenCapture ? (
                    <button type="button" className="op-btn" onClick={onOpenCapture}>
                      去数据采集
                    </button>
                  ) : null}
                  <details className="op-account-more">
                    <summary>更多</summary>
                    <div className="op-account-more-menu">
                      <button
                        type="button"
                        className="op-btn"
                        disabled={busy === account.id}
                        onClick={() => startEdit(account)}
                      >
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
                      <button
                        type="button"
                        className="op-btn"
                        disabled={busy === account.id}
                        onClick={() => void remove(account)}
                      >
                        删除
                      </button>
                    </div>
                  </details>
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
              {waitingAccountId === account.id ? (
                <div className="op-account-progress">
                  <small className="op-account-manual">
                    正在等待插件连接：请在刚打开的浏览器里登录工作台，并点一次「连接插件」，连接后会自动继续绑定。
                  </small>
                </div>
              ) : null}
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
                  {!links.staff_no ? (
                    <div className="op-links-staff">
                      <input
                        type="text"
                        value={staffNoDraft}
                        placeholder="员工标识（首次填写，用于团队汇总）"
                        onChange={(event) => setStaffNoDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") void saveStaffNo();
                        }}
                      />
                      <button
                        type="button"
                        className="op-btn op-btn-sm"
                        disabled={staffNoBusy}
                        onClick={() => void saveStaffNo()}
                      >
                        {staffNoBusy ? "保存中…" : "保存"}
                      </button>
                    </div>
                  ) : null}
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
