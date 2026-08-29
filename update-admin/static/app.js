const views = {
  login: document.querySelector("#login-view"),
  password: document.querySelector("#password-view"),
  dashboard: document.querySelector("#dashboard-view"),
};
const notice = document.querySelector("#notice");
const sessionActions = document.querySelector("#session-actions");
const currentUser = document.querySelector("#current-user");
const passwordTitle = document.querySelector("#password-title");
const cancelPasswordButton = document.querySelector("#cancel-password-button");
let activeUser = null;
const API_BASE = new URL("api/", window.location.href).pathname;
const RELEASE_STATUS_POLL_MS = 5000;

function showView(name) {
  Object.entries(views).forEach(([key, element]) => element.classList.toggle("hidden", key !== name));
  sessionActions.classList.toggle("hidden", name === "login");
}

function showNotice(message, type = "") {
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
  notice.classList.toggle("hidden", !message);
}

function errorMessage(payload, fallback) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

async function api(path, options = {}) {
  const requestPath = `${API_BASE}${String(path).replace(/^\//, "")}`;
  const response = await fetch(requestPath, {
    credentials: "same-origin",
    ...options,
    headers: options.body instanceof FormData ? options.headers : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* empty response */ }
  if (!response.ok) {
    const error = new Error(errorMessage(payload, `请求失败（${response.status}）`));
    error.status = response.status;
    error.code = payload?.detail?.code || "";
    throw error;
  }
  return payload;
}

function delay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("操作已取消", "AbortError"));
      return;
    }
    const timer = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("操作已取消", "AbortError"));
    }, { once: true });
  });
}

async function releaseStatus(version, signal) {
  return api(`releases/status/${encodeURIComponent(version)}`, { signal });
}

async function waitForPublishedVersion(version, signal) {
  while (!signal.aborted) {
    await delay(RELEASE_STATUS_POLL_MS, signal);
    try {
      const result = await releaseStatus(version, signal);
      if (result.published && result.release) return result.release;
    } catch (error) {
      if (error.name === "AbortError" || error.status === 401 || error.status === 403) throw error;
      // A transient status-query failure must not interrupt the active upload.
    }
  }
  throw new DOMException("操作已取消", "AbortError");
}

function setUser(user) {
  activeUser = user;
  currentUser.textContent = user ? `${user.username} · ${user.role}` : "";
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return "--";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function patchSummary(item) {
  const status = item.patch_status || "not_available";
  if (status === "published") {
    return `<span class="tag">已发布 · ${formatSize(Number(item.patch_total_bytes || 0))}</span><br><small>${escapeHtml(item.patch_from_version)} → ${escapeHtml(item.version)} · ${Number(item.patch_file_count || 0)} 个文件</small>`;
  }
  if (status === "failed") {
    return `<span class="tag warning" title="${escapeHtml(item.patch_error || "补丁生成失败")}">生成失败，使用完整包</span>`;
  }
  if (status === "prepared") return '<span class="tag">正在发布…</span>';
  if (status === "disabled") return '<span class="tag warning">未启用</span>';
  return '<span class="tag">无上一版本</span>';
}

function publishedMessage(version, patch) {
  if (patch?.status === "published" || patch?.patch_status === "published") {
    const size = Number(patch.total_bytes ?? patch.patch_total_bytes ?? 0);
    return `版本 ${version} 已发布；增量补丁 ${formatSize(size)} 已生成`;
  }
  if (patch?.status === "failed" || patch?.patch_status === "failed") {
    return `版本 ${version} 已发布；增量补丁生成失败，用户将使用完整安装包`;
  }
  return `版本 ${version} 已发布`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

async function loadDashboard() {
  const [releases, audit] = await Promise.all([api("releases"), api("audit-logs")]);
  const releaseRows = document.querySelector("#release-rows");
  releaseRows.innerHTML = releases.items.length ? releases.items.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.version)}</strong></td>
      <td>${escapeHtml(item.created_by)}</td>
      <td>${escapeHtml(item.published_at)}</td>
      <td><span class="tag ${item.mandatory ? "warning" : ""}">${item.mandatory ? "强制" : "可跳过"}</span></td>
      <td>${escapeHtml(item.installer_filename)}<br><small>${formatSize(item.file_size)}</small></td>
      <td>${patchSummary(item)}</td>
      <td><span class="hash" title="${escapeHtml(item.sha256)}">${escapeHtml(item.sha256)}</span></td>
      <td>${escapeHtml(item.authenticode_status)}</td>
    </tr>`).join("") : '<tr><td class="empty" colspan="8">尚未发布任何版本</td></tr>';

  const auditRows = document.querySelector("#audit-rows");
  auditRows.innerHTML = audit.items.length ? audit.items.map((item) => `
    <tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.username)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.target)}</td><td>${escapeHtml(item.ip_address)}</td></tr>`).join("") : '<tr><td class="empty" colspan="5">暂无操作记录</td></tr>';
}

async function enterForUser(user) {
  setUser(user);
  if (user.must_change_password) {
    passwordTitle.textContent = "首次登录，请修改密码";
    cancelPasswordButton.classList.add("hidden");
    showView("password");
    return;
  }
  showView("dashboard");
  await loadDashboard();
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showNotice("");
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    const result = await api("auth/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
    formElement.reset();
    await enterForUser(result.user);
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showNotice("");
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  if (form.get("new_password") !== form.get("confirm_password")) {
    showNotice("两次输入的新密码不一致", "error");
    return;
  }
  try {
    await api("auth/change-password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password"), new_password: form.get("new_password") }) });
    formElement.reset();
    setUser(null);
    showView("login");
    showNotice("密码已修改，请使用新密码重新登录", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#change-password-button").addEventListener("click", () => {
  passwordTitle.textContent = "修改登录密码";
  cancelPasswordButton.classList.remove("hidden");
  showView("password");
  showNotice("");
});

cancelPasswordButton.addEventListener("click", () => {
  showView("dashboard");
  showNotice("");
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try { await api("auth/logout", { method: "POST", body: "{}" }); } catch (_) { /* cookie may already be expired */ }
  setUser(null);
  showView("login");
  showNotice("");
});

document.querySelector("#refresh-button").addEventListener("click", async () => {
  try { await loadDashboard(); showNotice("已刷新", "success"); } catch (error) { showNotice(error.message, "error"); }
});

document.querySelector("#publish-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showNotice("");
  const button = document.querySelector("#publish-button");
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const version = String(form.get("version") || "").trim();
  form.set("mandatory", form.get("mandatory") ? "true" : "false");
  const publishController = new AbortController();
  const monitorController = new AbortController();
  button.disabled = true;
  button.textContent = "正在上传、签名并校验…";
  try {
    const publishRequest = api("releases/publish", {
      method: "POST",
      body: form,
      signal: publishController.signal,
    }).then((result) => ({ kind: "response", result }))
      .catch((error) => ({ kind: "error", error }));
    const statusMonitor = waitForPublishedVersion(version, monitorController.signal)
      .then((release) => ({ kind: "confirmed", release }))
      .catch((error) => ({ kind: "monitor-error", error }));
    const outcome = await Promise.race([publishRequest, statusMonitor]);
    monitorController.abort();

    if (outcome.kind === "confirmed") {
      // Publishing is durable once the exact version appears in release history.
      // Stop waiting for a response that may have been lost between Nginx and the browser.
      publishController.abort();
      formElement.reset();
      showNotice(`${publishedMessage(outcome.release.version, outcome.release)}（服务器状态已确认）`, "success");
      await loadDashboard();
      return;
    }
    if (outcome.kind === "monitor-error") throw outcome.error;
    if (outcome.kind === "error") {
      // The response can be lost after an atomic publish. Check durable state once
      // before showing an error so the administrator never retries a successful version.
      const durable = await releaseStatus(version).catch(() => null);
      if (durable?.published && durable.release) {
        formElement.reset();
        showNotice(`${publishedMessage(durable.release.version, durable.release)}（服务器状态已确认）`, "success");
        await loadDashboard();
        return;
      }
      throw outcome.error;
    }
    const result = outcome.result;
    formElement.reset();
    const warning = result.authenticode?.status === "Valid" ? "" : `；代码签名状态：${result.authenticode?.status || "未知"}`;
    showNotice(`${publishedMessage(result.release.version, result.patch)}${warning}`, warning ? "" : "success");
    await loadDashboard();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    monitorController.abort();
    button.disabled = false;
    button.textContent = "上传、签名并发布";
  }
});

(async function bootstrap() {
  try {
    const result = await api("auth/me");
    await enterForUser(result.user);
  } catch (_) {
    setUser(null);
    showView("login");
  }
})();
