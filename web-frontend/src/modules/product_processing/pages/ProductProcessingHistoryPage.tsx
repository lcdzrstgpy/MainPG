import { useEffect, useMemo, useState } from "react";

import { ppRequest, type ApiContext } from "../api/client";
import { productProcessingApiContext } from "../api/context";
import type { TaskHistoryItem } from "../types";
import "../styles/ProductProcessingVerifyPage.css";

const API_BASE = "/api/product-processing";
const HISTORY_PAGE_SIZE = 20;

type Props = {
  onOpenTask: (taskId: number) => void;
  onOpenPrecheck: (taskId: number) => void;
};

function api(): ApiContext {
  return productProcessingApiContext();
}

function statusLabel(status: string): string {
  return ({
    queued: "等待处理",
    running: "处理中",
    paused: "已暂停",
    cancelled: "已终止·保留成功项",
    completed: "已完成",
    completed_with_review: "完成·待确认",
    partial_failure: "部分失败",
    failed: "任务失败",
  } as Record<string, string>)[status] || status;
}

function formatDuration(seconds?: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${secs}秒`;
  return `${secs}秒`;
}

/** 列表左侧的状态徽标图标：与状态文案同源，只做视觉强化，不重复无障碍语义。 */
function statusIcon(status: string): string {
  if (status === "completed" || status === "completed_with_review") return "icon-check-circle";
  if (status === "running") return "icon-sync";
  if (status === "queued" || status === "paused") return "icon-time-circle";
  if (status === "partial_failure") return "icon-file-exception";
  if (status === "failed") return "icon-close-circle";
  if (status === "cancelled") return "icon-undo";
  return "icon-filedone";
}

function canEnterPrecheck(task: TaskHistoryItem): boolean {
  return task.success_count > 0 && ["completed", "completed_with_review", "partial_failure", "cancelled"].includes(task.status);
}

/**
 * 历史批次的展示名：批次号 + 采集批次名（与「采集批次」下拉同一套命名，
 * 如「选品·1688·狗衣服·11-12 14:08」）。旧版每行都叫「产品处理任务」，
 * 既认不出批次也看不出处理了什么。
 */
function taskDisplayName(task: TaskHistoryItem): string {
  const names = task.batch_names ?? [];
  if (!names.length) return `#${task.task_id} · 手动导入`;
  const extra = names.length > 1 ? ` +${names.length - 1} 个批次` : "";
  return `#${task.task_id} · ${names[0]}${extra}`;
}

/** 悬停提示：列出全部采集批次，再补前几个商品的「货号 + 标题」。 */
function taskDisplayHint(task: TaskHistoryItem): string {
  const samples = (task.sample_items || []).filter((item) => item.title.trim());
  const lines = [
    ...(task.batch_names ?? []).map((name) => `批次：${name}`),
    ...samples.map((item) => (item.skc ? `${item.skc} ${item.title}` : item.title)),
  ];
  return lines.join("\n") || task.title || `处理任务 #${task.task_id}`;
}

export function ProductProcessingHistoryPage({ onOpenTask, onOpenPrecheck }: Props) {
  const ctx = api();
  const [tasks, setTasks] = useState<TaskHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const totalPages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const summary = useMemo(() => ({
    active: tasks.filter((task) => ["queued", "running", "paused"].includes(task.status)).length,
    completed: tasks.filter((task) => task.status === "completed").length,
  }), [tasks]);

  const loadHistory = async (nextPage = page, nextDateFrom = dateFrom, nextDateTo = dateTo) => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      limit: String(HISTORY_PAGE_SIZE),
      offset: String((nextPage - 1) * HISTORY_PAGE_SIZE),
    });
    if (nextDateFrom) params.set("date_from", nextDateFrom);
    if (nextDateTo) params.set("date_to", nextDateTo);
    try {
      const data = await ppRequest<{ tasks: TaskHistoryItem[]; total?: number }>(ctx, `${API_BASE}/tasks/history?${params}`);
      setTasks(data.tasks || []);
      setTotal(data.total || 0);
    } catch (cause) {
      setTasks([]);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadHistory(1);
    // 仅在页面首次进入时加载；筛选和翻页由用户操作触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyFilters = () => {
    setPage(1);
    void loadHistory(1, dateFrom, dateTo);
  };

  const clearFilters = () => {
    setDateFrom("");
    setDateTo("");
    setPage(1);
    void loadHistory(1, "", "");
  };

  const goToPage = (nextPage: number) => {
    const resolved = Math.min(Math.max(1, nextPage), totalPages);
    setPage(resolved);
    void loadHistory(resolved);
  };

  return (
    <div className="verify-page processing-history-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>历史记录</h1>
          <p>所有 AI 处理批次都会保存在这里；关闭任务页不会中断后台处理，可随时重新打开查看进度、结果和输出文件。</p>
        </div>
        <div className="processing-history-hero-visual">
          <svg className="processing-history-hero-art" viewBox="0 0 132 66" aria-hidden="true" focusable="false">
            <g fill="none" stroke="rgba(255, 255, 255, .62)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <rect x="30" y="18" width="46" height="30" rx="9" fill="rgba(255, 255, 255, .1)" />
              <rect x="21" y="12" width="46" height="30" rx="9" fill="rgba(255, 255, 255, .16)" stroke="rgba(255, 255, 255, .82)" />
              <path d="M31 24h26M31 31h17" stroke="rgba(255, 255, 255, .6)" />
              <circle cx="94" cy="22" r="13" fill="rgba(255, 255, 255, .12)" stroke="rgba(255, 255, 255, .78)" />
              <path d="M94 15v7l5 3.4" stroke="rgba(255, 255, 255, .95)" />
              <circle cx="74" cy="50" r="7" fill="rgba(255, 255, 255, .2)" stroke="rgba(255, 255, 255, .7)" />
              <path d="M70.6 50l2.4 2.4 4.4-5.2" stroke="rgba(255, 255, 255, .95)" />
              <path d="M46 48l1.5 3.4 3.4 1.5-3.4 1.5L46 58l-1.5-3.6-3.4-1.5 3.4-1.5z" stroke="rgba(255, 255, 255, .55)" strokeWidth="1.2" />
            </g>
          </svg>
          <div className="processing-history-summary" aria-label="当前页任务概览">
            <span>处理中 <b>{summary.active}</b></span>
            <span>已完成 <b>{summary.completed}</b></span>
          </div>
        </div>
      </header>

      <section className="verify-section processing-history-toolbar">
        <div className="processing-history-date-range" aria-label="日期范围">
          <span className="processing-history-filter-label">日期范围</span>
          <input aria-label="起始日期" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          <span className="processing-history-date-divider" aria-hidden="true">→</span>
          <input aria-label="结束日期" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
        </div>
        <div className="verify-actions">
          <button type="button" className="primary" onClick={applyFilters}>筛选</button>
          <button type="button" className="processing-history-clear" onClick={clearFilters}>清除</button>
          <button type="button" className="processing-history-refresh" onClick={() => void loadHistory()} disabled={loading}>{loading ? <i className="app-spinner is-sm" aria-hidden="true" /> : <i className="iconfont icon-sync" aria-hidden="true" />}刷新</button>
        </div>
      </section>

      {error && <div className="verify-message error">{error}</div>}
      <section className="verify-section processing-history-list" aria-label="AI处理历史记录">
        <div className="verify-section-head">
          <h2>AI 处理批次</h2>
          <span className="verify-sub">共 {total} 条</span>
        </div>
        {loading && (
          <div className="app-loading-block">
            <i className="app-spinner" aria-hidden="true" />
            正在读取历史任务…
          </div>
        )}
        {!loading && tasks.length === 0 && (
          <div className="processing-history-empty">
            <svg className="processing-history-empty-art" viewBox="0 0 150 96" aria-hidden="true" focusable="false">
              <defs>
                <linearGradient id="processingHistoryEmptyStroke" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#3aa9d8" />
                  <stop offset="1" stopColor="#1fbfa6" />
                </linearGradient>
              </defs>
              <g fill="none" stroke="url(#processingHistoryEmptyStroke)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="42" y="30" width="56" height="36" rx="10" fill="rgba(56, 169, 214, .08)" />
                <path d="M52 44h30M52 52h20" stroke="rgba(56, 169, 214, .5)" />
                <circle cx="104" cy="30" r="13" fill="rgba(25, 189, 163, .1)" />
                <path d="M104 22v8l5 3" />
                <circle cx="62" cy="72" r="6.5" fill="rgba(25, 189, 163, .12)" />
                <path d="M59 72l2.3 2.3 4-4.8" />
                <path d="M124 54l1.7 3.9 3.9 1.7-3.9 1.7L124 65l-1.7-3.7-3.9-1.7 3.9-1.7z" strokeWidth="1.2" opacity=".8" />
                <path d="M26 22l1.3 3 3 1.3-3 1.3L26 31l-1.3-3.4-3-1.3 3-1.3z" strokeWidth="1.2" opacity=".55" />
                <path d="M38 78h74" strokeDasharray="3 6" opacity=".45" />
              </g>
            </svg>
            <strong>还没有处理批次</strong>
            <p>从「AI 处理」提交第一个批次后，进度、耗时和输出文件都会自动归档在这里。</p>
          </div>
        )}
        {!loading && tasks.length > 0 && (
          <ul className="processing-history-items">
            {tasks.map((task) => (
              <li key={task.task_id} className={`status-${task.status}`}>
                <span className="processing-history-status" aria-hidden="true">
                  <i className={`iconfont ${statusIcon(task.status)}`} />
                </span>
                <div className="processing-history-primary">
                  <strong title={taskDisplayHint(task)}>{taskDisplayName(task)}</strong>
                  <span className={`verify-badge status-${task.status}`}>{statusLabel(task.status)}</span>
                  <span>{task.target_site} · {task.target_language_label}</span>
                </div>
                <div className="processing-history-meta">
                  <span>共 <b>{task.total_count}</b> · 成功 <b className="ok">{task.success_count}</b> · 失败 <b className="bad">{task.failed_count}</b>{task.skipped_count > 0 && <> · 跳过 <b>{task.skipped_count}</b></>}</span>
                  <span>耗时 {formatDuration(task.elapsed_seconds)}</span>
                  <time dateTime={task.created_at}>
                    {/* created_at 为空/非法时不能直接 toLocaleString，否则渲染成 "Invalid Date" */}
                    {Number.isNaN(new Date(task.created_at).getTime())
                      ? (task.created_at || "—")
                      : new Date(task.created_at).toLocaleString("zh-CN")}
                  </time>
                  <span>
                    {task.downloadable.dxm
                      ? "输出已生成"
                      : canEnterPrecheck(task)
                        ? "待预检导出"
                        : "暂未生成输出"}
                  </span>
                </div>
                <div className="processing-history-actions">
                  <button type="button" onClick={() => onOpenTask(task.task_id)}>继续查看</button>
                  {canEnterPrecheck(task) && <button type="button" className="primary" onClick={() => onOpenPrecheck(task.task_id)}>进入预检</button>}
                </div>
              </li>
            ))}
          </ul>
        )}
        {totalPages > 1 && (
          <footer className="verify-pagination processing-history-pagination">
            <button type="button" onClick={() => goToPage(page - 1)} disabled={page <= 1 || loading}>上一页</button>
            <span>第 {page} / {totalPages} 页</span>
            <button type="button" onClick={() => goToPage(page + 1)} disabled={page >= totalPages || loading}>下一页</button>
          </footer>
        )}
      </section>
    </div>
  );
}

export default ProductProcessingHistoryPage;
