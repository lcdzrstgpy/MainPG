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
const publishProgress = document.querySelector("#publish-progress");
const progressVersion = document.querySelector("#progress-version");
const progressPhase = document.querySelector("#progress-phase");
const progressPercent = document.querySelector("#progress-percent");
const progressBar = document.querySelector("#progress-bar");
const progressBytes = document.querySelector("#progress-bytes");
const progressMessage = document.querySelector("#progress-message");
let activeUser = null;
let activePublishJobId = null;
let publishPollTimer = null;
let releasePage = 1;
let auditPage = 1;
const API_BASE = new URL("api/", window.location.href).pathname;
const PUBLISH_JOB_POLL_MS = 1000;
const PUBLISH_PHASES = ["uploading", "evsign", "authenticode", "patching", "publishing", "completed"];
const PHASE_LABELS = {
  uploading: "正在上传",
  evsign: "EV Sign 签名中",
  authenticode: "Authenticode 验证中",
  patching: "增量补丁生成中",
  publishing: "更新文件发布中",
  completed: "清单签名完成",
  failed: "发布失败",
};
const PHASE_PERCENT = { evsign: 28, authenticode: 44, patching: 62, publishing: 82, completed: 100, failed: 100 };

function channelLabel(channel) {
  if (channel === "public") return "公共版";
  if (channel === "internal") return "内测版";
  return "仅软件更新";
}

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

async function releaseStatus(version, signal) {
  return api(`releases/status/${encodeURIComponent(version)}`, { signal });
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

function publishPercent(job) {
  if (job.phase === "uploading") {
    const total = Number(job.total_bytes || 0);
    const uploaded = Number(job.uploaded_bytes || 0);
    return total > 0 ? Math.min(20, Math.max(0, (uploaded / total) * 20)) : 0;
  }
  return PHASE_PERCENT[job.phase] ?? 0;
}

function renderPublishJob(job) {
  if (!job) {
    publishProgress.classList.add("hidden");
    return;
  }
  const percent = publishPercent(job);
  const failed = job.phase === "failed";
  const activeIndex = PUBLISH_PHASES.indexOf(failed ? (job.failed_phase || "uploading") : job.phase);
  publishProgress.classList.remove("hidden");
  publishProgress.classList.toggle("is-error", failed);
  progressVersion.textContent = `版本 ${job.version} · ${channelLabel(job.channel)}`;
  progressPhase.textContent = PHASE_LABELS[job.phase] || job.phase;
  progressPercent.textContent = `总进度 ${Math.round(percent)}%`;
  progressBar.style.width = `${percent}%`;
  progressBytes.textContent = job.total_bytes > 0
    ? `${formatSize(Number(job.uploaded_bytes || 0))} / ${formatSize(Number(job.total_bytes || 0))}`
    : "等待安装包";
  progressMessage.textContent = failed ? (job.error || job.message || "发布失败") : (job.message || "");
  document.querySelectorAll("#progress-steps li").forEach((item, index) => {
    item.classList.remove("is-done", "is-active", "is-error");
    if (failed) {
      if (index < Math.max(activeIndex, 0)) item.classList.add("is-done");
      else if (index === Math.max(activeIndex, 0)) item.classList.add("is-error");
      return;
    }
    if (job.phase === "completed" || index < activeIndex) item.classList.add("is-done");
    else if (index === activeIndex) item.classList.add("is-active");
  });
}

function stopPublishPolling() {
  if (publishPollTimer !== null) window.clearTimeout(publishPollTimer);
  publishPollTimer = null;
}

async function pollPublishJob(jobId) {
  if (!jobId || activePublishJobId !== jobId) return;
  try {
    const result = await api(`publish-jobs/${encodeURIComponent(jobId)}`);
    renderPublishJob(result.job);
    if (["completed", "failed"].includes(result.job.phase)) {
      activePublishJobId = null;
      stopPublishPolling();
      return;
    }
  } catch (error) {
    if (error.status === 401 || error.status === 403 || error.status === 404) {
      activePublishJobId = null;
      stopPublishPolling();
      return;
    }
  }
  publishPollTimer = window.setTimeout(() => pollPublishJob(jobId), PUBLISH_JOB_POLL_MS);
}

function startPublishPolling(jobId) {
  stopPublishPolling();
  activePublishJobId = jobId;
  publishPollTimer = window.setTimeout(() => pollPublishJob(jobId), PUBLISH_JOB_POLL_MS);
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForPublishJobTerminal(jobId, onJob, isCancelled) {
  while (!isCancelled()) {
    await delay(PUBLISH_JOB_POLL_MS);
    if (isCancelled()) return null;
    const result = await api(`publish-jobs/${encodeURIComponent(jobId)}`);
    onJob(result.job);
    if (["completed", "failed"].includes(result.job.phase)) return result.job;
  }
  return null;
}

function uploadRelease(form, job, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}releases/publish`);
    xhr.withCredentials = true;
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      onProgress(event.loaded, event.total);
    });
    xhr.addEventListener("load", () => {
      const payload = xhr.response && typeof xhr.response === "object" ? xhr.response : {};
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      const error = new Error(errorMessage(payload, `请求失败（${xhr.status}）`));
      error.status = xhr.status;
      error.code = payload?.detail?.code || "";
      reject(error);
    });
    xhr.addEventListener("error", () => reject(new Error("上传连接中断，请检查发布任务状态")));
    xhr.addEventListener("abort", () => reject(new Error("上传已取消")));
    form.set("job_id", job.id);
    form.set("channel", job.channel || "update_only");
    xhr.send(form);
  });
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

function publishedMessage(version, patch, channel = "update_only") {
  const prefix = channel === "update_only"
    ? `版本 ${version} 已发布；官网下载文件未变更`
    : `${channelLabel(channel)}版本 ${version} 已发布并同步官网下载`;
  if (patch?.status === "published" || patch?.patch_status === "published") {
    const size = Number(patch.total_bytes ?? patch.patch_total_bytes ?? 0);
    return `${prefix}；增量补丁 ${formatSize(size)} 已生成`;
  }
  if (patch?.status === "failed" || patch?.patch_status === "failed") {
    return `${prefix}；增量补丁生成失败，用户将使用完整安装包`;
  }
  return prefix;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function renderPagination(containerId, payload, onPageChange) {
  const container = document.querySelector(containerId);
  const page = Number(payload.page || 1);
  const pages = Math.max(1, Number(payload.pages || 1));
  const total = Number(payload.total || 0);
  const pageSize = Number(payload.page_size || 0);
  container.replaceChildren();

  const summary = document.createElement("span");
  summary.className = "pagination-summary";
  summary.textContent = `共 ${total} 条 · 每页 ${pageSize} 条 · 第 ${page} / ${pages} 页`;
  container.append(summary);

  const addButton = (label, targetPage, { disabled = false, current = false, title = "" } = {}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.disabled = disabled;
    button.classList.toggle("is-current", current);
    if (current) button.setAttribute("aria-current", "page");
    if (title) button.title = title;
    button.addEventListener("click", async () => {
      if (disabled || current) return;
      try {
        await onPageChange(targetPage);
      } catch (error) {
        showNotice(error.message, "error");
      }
    });
    container.append(button);
  };

  addButton("首页", 1, { disabled: page <= 1, title: "第一页" });
  addButton("上一页", page - 1, { disabled: page <= 1 });
  const start = Math.max(1, page - 2);
  const end = Math.min(pages, page + 2);
  for (let current = start; current <= end; current += 1) {
    addButton(String(current), current, { current: current === page });
  }
  addButton("下一页", page + 1, { disabled: page >= pages });
  addButton("末页", pages, { disabled: page >= pages, title: "最后一页" });
}

async function loadReleasePage(page = releasePage) {
  const releases = await api(`releases?page=${encodeURIComponent(page)}`);
  releasePage = Number(releases.page || 1);
  const releaseRows = document.querySelector("#release-rows");
  releaseRows.innerHTML = releases.items.length ? releases.items.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.version)}</strong></td>
      <td><span class="tag">${channelLabel(item.channel)}</span></td>
      <td>${escapeHtml(item.created_by)}</td>
      <td>${escapeHtml(item.published_at)}</td>
      <td><span class="tag ${item.mandatory ? "warning" : ""}">${item.mandatory ? "强制" : "可跳过"}</span></td>
      <td>${escapeHtml(item.installer_filename)}<br><small>${formatSize(item.file_size)}</small></td>
      <td>${patchSummary(item)}</td>
      <td><span class="hash" title="${escapeHtml(item.sha256)}">${escapeHtml(item.sha256)}</span></td>
      <td>${escapeHtml(item.authenticode_status)}</td>
    </tr>`).join("") : '<tr><td class="empty" colspan="9">尚未发布任何版本</td></tr>';
  renderPagination("#release-pagination", releases, async (nextPage) => {
    await loadReleasePage(nextPage);
  });
}

async function loadAuditPage(page = auditPage) {
  const audit = await api(`audit-logs?page=${encodeURIComponent(page)}`);
  auditPage = Number(audit.page || 1);
  const auditRows = document.querySelector("#audit-rows");
  auditRows.innerHTML = audit.items.length ? audit.items.map((item) => `
    <tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.username)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.target)}</td><td>${escapeHtml(item.ip_address)}</td></tr>`).join("") : '<tr><td class="empty" colspan="5">暂无操作记录</td></tr>';
  renderPagination("#audit-pagination", audit, async (nextPage) => {
    await loadAuditPage(nextPage);
  });
}

async function loadDashboard() {
  const [, , jobs] = await Promise.all([
    loadReleasePage(),
    loadAuditPage(),
    api("publish-jobs"),
  ]);
  const latestJob = jobs.items[0] || null;
  if (latestJob) {
    renderPublishJob(latestJob);
    if (!["completed", "failed"].includes(latestJob.phase)) startPublishPolling(latestJob.id);
  }
}

async function enterForUser(user) {
  setUser(user);
  if (user.must_change_password) {
    passwordTitle.textContent = "首次登录，请修改密码";
    cancelPasswordButton.classList.add("hidden");
    showView("password");
    return;
  }
  releasePage = 1;
  auditPage = 1;
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
  stopPublishPolling();
  activePublishJobId = null;
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
  const publishButtons = Array.from(document.querySelectorAll(".publish-channel-actions button[type='submit']"));
  const button = event.submitter instanceof HTMLButtonElement
    ? event.submitter
    : document.querySelector("#publish-update-only-button");
  const channel = ["internal", "public"].includes(button?.value)
    ? button.value
    : "update_only";
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const version = String(form.get("version") || "").trim();
  const installer = form.get("installer");
  if (!(installer instanceof File) || installer.size <= 0) {
    showNotice("请选择有效的 EXE 安装包", "error");
    return;
  }
  form.set("mandatory", form.get("mandatory") ? "true" : "false");
  form.set("channel", channel);
  publishButtons.forEach((item) => { item.disabled = true; });
  button.textContent = "正在创建发布任务…";
  let monitorCancelled = true;
  try {
    const created = await api("publish-jobs", {
      method: "POST",
      body: JSON.stringify({
        version,
        channel,
        mandatory: form.get("mandatory") === "true",
        release_notes: String(form.get("release_notes") || ""),
        installer_filename: installer.name,
        total_bytes: installer.size,
      }),
    });
    const job = created.job;
    renderPublishJob(job);
    button.textContent = "正在上传安装包…";
    let lastReportedAt = 0;
    monitorCancelled = false;
    const uploadPromise = uploadRelease(form, job, (uploaded, total) => {
      renderPublishJob({ ...job, uploaded_bytes: uploaded, total_bytes: total, phase: "uploading", message: "正在上传安装包" });
      const now = Date.now();
      if (now - lastReportedAt >= 1000 || uploaded === total) {
        lastReportedAt = now;
        api(`publish-jobs/${encodeURIComponent(job.id)}/upload-progress`, {
          method: "POST",
          body: JSON.stringify({ uploaded_bytes: uploaded, total_bytes: total }),
        }).catch(() => {});
      }
    });
    const terminalPromise = waitForPublishJobTerminal(
      job.id,
      (nextJob) => {
        renderPublishJob(nextJob);
        button.textContent = nextJob.phase === "completed"
          ? "发布完成"
          : nextJob.phase === "failed"
            ? "发布失败"
            : (PHASE_LABELS[nextJob.phase] || "正在处理…");
      },
      () => monitorCancelled,
    );
    const outcome = await Promise.race([
      uploadPromise.then((result) => ({ source: "response", result })),
      terminalPromise.then((terminalJob) => ({ source: "job", terminalJob })),
    ]);
    monitorCancelled = true;
    if (outcome.source === "job") {
      if (outcome.terminalJob?.phase === "failed") {
        throw new Error(outcome.terminalJob.error || outcome.terminalJob.message || "发布失败");
      }
      const durable = await releaseStatus(version);
      if (durable?.published && durable.release) {
        formElement.reset();
        showNotice(`${publishedMessage(durable.release.version, durable.release, durable.release.channel)}（服务器状态已确认）`, "success");
        await loadDashboard();
        return;
      }
      throw new Error("任务已完成，但暂未读取到发布记录，请刷新后确认");
    }
    const result = outcome.result;
    if (!result?.release) {
      const durable = await releaseStatus(version).catch(() => null);
      if (durable?.published && durable.release) {
        formElement.reset();
        showNotice(`${publishedMessage(durable.release.version, durable.release, durable.release.channel)}（服务器状态已确认）`, "success");
        await loadDashboard();
        return;
      }
      throw new Error("服务器未返回发布结果，请查看任务状态");
    }
    formElement.reset();
    const warning = result.authenticode?.status === "Valid" ? "" : `；代码签名状态：${result.authenticode?.status || "未知"}`;
    showNotice(`${publishedMessage(result.release.version, result.patch, result.channel || channel)}${warning}`, warning ? "" : "success");
    await loadDashboard();
  } catch (error) {
    const durable = await releaseStatus(version).catch(() => null);
    if (durable?.published && durable.release) {
      formElement.reset();
      showNotice(`${publishedMessage(durable.release.version, durable.release, durable.release.channel)}（服务器状态已确认）`, "success");
      await loadDashboard();
    } else {
      showNotice(error.message, "error");
    }
  } finally {
    monitorCancelled = true;
    activePublishJobId = null;
    stopPublishPolling();
    publishButtons.forEach((item) => { item.disabled = false; });
    document.querySelector("#publish-update-only-button").textContent = "只发布更新（不同步官网）";
    document.querySelector("#publish-internal-button").textContent = "上传并发布内测版";
    document.querySelector("#publish-public-button").textContent = "上传并发布公共版";
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
