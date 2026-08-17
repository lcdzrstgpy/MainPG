import { useEffect, useMemo, useState } from "react";

import { ppRequest, type ApiContext } from "../api/client";
import type { TaskHistoryItem } from "../types";
import "../styles/ProductProcessingVerifyPage.css";

const API_BASE = "/api/product-processing";
const HISTORY_PAGE_SIZE = 20;

type Props = {
  onOpenTask: (taskId: number) => void;
  onOpenPrecheck: (taskId: number) => void;
};

function api(): ApiContext {
  return { baseUrl: "", token: "", workspaceId: "default" };
}

function statusLabel(status: string): string {
  return ({
    queued: "等待处理",
    running: "处理中",
    paused: "已暂停",
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

function canEnterPrecheck(task: TaskHistoryItem): boolean {
  return task.success_count > 0 && ["completed", "completed_with_review", "partial_failure"].includes(task.status);
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
          <span className="verify-eyebrow">AI PROCESSING · HISTORY</span>
          <h1>历史记录</h1>
          <p>所有 AI 处理批次都会保存在这里；关闭任务页不会中断后台处理，可随时重新打开查看进度、结果和输出文件。</p>
        </div>
        <div className="processing-history-summary" aria-label="当前页任务概览">
          <span>处理中 <b>{summary.active}</b></span>
          <span>已完成 <b>{summary.completed}</b></span>
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
          <button type="button" className="processing-history-refresh" onClick={() => void loadHistory()} disabled={loading}><i className="iconfont icon-sync" aria-hidden="true" />刷新</button>
        </div>
      </section>

      {error && <div className="verify-message error">{error}</div>}
      <section className="verify-section processing-history-list" aria-label="AI处理历史记录">
        <div className="verify-section-head">
          <h2>AI 处理批次</h2>
          <span className="verify-sub">共 {total} 条</span>
        </div>
        {loading && <p className="verify-empty">正在读取历史任务…</p>}
        {!loading && tasks.length === 0 && <p className="verify-empty">暂无 AI 处理记录。开始处理后，批次会自动保存在这里。</p>}
        {!loading && tasks.length > 0 && (
          <ul className="processing-history-items">
            {tasks.map((task) => (
              <li key={task.task_id}>
                <div className="processing-history-primary">
                  <strong title={task.title}>{task.title || `处理任务 #${task.task_id}`}</strong>
                  <span className={`verify-badge status-${task.status}`}>{statusLabel(task.status)}</span>
                  <span>{task.target_site} · {task.target_language_label}</span>
                </div>
                <div className="processing-history-meta">
                  <span>共 <b>{task.total_count}</b> · 成功 <b className="ok">{task.success_count}</b> · 失败 <b className="bad">{task.failed_count}</b>{task.skipped_count > 0 && <> · 跳过 <b>{task.skipped_count}</b></>}</span>
                  <span>耗时 {formatDuration(task.elapsed_seconds)}</span>
                  <time dateTime={task.created_at}>{new Date(task.created_at).toLocaleString("zh-CN")}</time>
                  <span>{task.has_downloadable_output ? "输出已生成" : "暂未生成输出"}</span>
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
