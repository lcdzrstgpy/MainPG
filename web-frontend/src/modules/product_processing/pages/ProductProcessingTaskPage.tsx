import { useEffect, useMemo, useState } from 'react';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import type {
  ProductProcessingOptions,
  TaskHistoryItem,
  TaskOutputsResponse,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

const SITES = [
  { code: 'US', label: '美国站' },
  { code: 'CO', label: '哥伦比亚站' },
  { code: 'EC', label: '厄瓜多尔站' },
] as const;

const SCOPES: { key: string; label: string }[] = [
  { key: 'title', label: '标题' },
  { key: 'details', label: '详情' },
  { key: 'product_dimensions', label: '物流包裹尺寸/重量' },
  { key: 'four_grid', label: '四宫格' },
  { key: 'detail_images', label: '详情图' },
  { key: 'qualification', label: '资质' },
];

// 生图提示词模板（对齐后端 IMAGE_TEMPLATES：A/B 直观标题区分两套生图逻辑）
const IMAGE_TEMPLATES: { id: 'A' | 'B'; name: string; description: string }[] = [
  { id: 'A', name: '标准商品海报', description: 'Amazon 高级电商视觉：大主体主图 + 精品展示 + 生活方式场景，含英文卖点文案' },
  { id: 'B', name: '高端模特视觉（防比价）', description: '人设+空间故事叙事、杂志编辑大片感，材质显贵、难以搜图比价，画面无文字' },
];

const IMAGE_GENERATION_OPTIONS: { count: 1 | 2 | 4; name: string; description: string }[] = [
  { count: 1, name: '单图 × 4（推荐）', description: '4 次并发生图，不拆分，成图最稳' },
  { count: 2, name: '双图 × 2', description: '2 次并发生图，每次拆成两张，速度与稳定性平衡' },
  { count: 4, name: '四宫格 × 1', description: '仅 1 次调用后拆四张，最省调用但更依赖分格效果' },
];

const FAILURE_CLASS_LABELS: Record<string, string> = {
  technical_retryable: '技术失败可重试',
  configuration_blocked: '配置阻断',
  identity_review_required: '身份待复核',
  logistics_review_required: '尺寸待复核',
};

const HISTORY_PAGE_SIZE = 10;

// AI 配置/额度类失败（401 key 无效、403 权限、402/429 额度或限流、key 未配置、连接不可达等），
// 用户需要去「系统配置」检查 AI key 或账户余额，而不是修改商品数据。
const AI_CONFIG_ERROR_RE =
  /HTTP\s+40[1239]|api\s+key\s+is\s+not\s+configured|insufficient.*(?:quota|balance)|quota|balance|unauthorized|forbidden|unreachable|invalid.*(?:api\s*key|credentials)/i;

function isAiConfigError(reason?: string): boolean {
  if (!reason) return false;
  return AI_CONFIG_ERROR_RE.test(reason);
}

const AI_CONFIG_HINT =
  '似乎 api key 配置有问题哦，可以先去系统配置保存一下或者检查一下余额亲~（当前失败为 AI 服务鉴权/额度问题，与商品数据无关）';

type Props = {
  initialDraftIds?: number[];
  initialOptions?: ProductProcessingOptions;
  /** 任务完成后打开预检页（生成表格 → 预检修改 → 导出最终版） */
  onOpenPrecheck?: (taskId: number) => void;
};

function api(): ApiContext {
  return { baseUrl: '', token: '', workspaceId: 'default' };
}

function taskStatusLabel(status: string): string {
  return ({
    queued: '等待处理',
    running: '处理中',
    paused: '已暂停',
    completed: '已完成',
    completed_with_review: '完成，仍有待确认项',
    failed: '任务失败',
  })[status] || status;
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

export function ProductProcessingTaskPage({ initialDraftIds, initialOptions, onOpenPrecheck }: Props) {
  const ctx = api();
  const [options, setOptions] = useState<ProductProcessingOptions>(
    initialOptions || {
      targetSite: 'US',
      targetLanguage: 'en',
      maxProducts: 0,
      processingScope: ['title', 'details', 'product_dimensions', 'four_grid', 'detail_images', 'qualification'],
      qualificationMode: 'standard',
      includeProductVideo: false,
      skipDuplicates: false,
      ipCheck: true,
      maxParallelDrafts: 8,
      imageTemplate: 'A',
      imageGenerationCount: 1,
    }
  );
  const [batch, setBatch] = useState<TaskOutputsResponse | null>(null);
  const [history, setHistory] = useState<TaskHistoryItem[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyDateFrom, setHistoryDateFrom] = useState('');
  const [historyDateTo, setHistoryDateTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const failureItems = useMemo(
    () => batch?.items.filter(
      (item) => item.status === 'attention_required' || item.status === 'failed'
    ) || [],
    [batch]
  );

  // 是否存在 AI 配置/额度类失败（此时应提示用户去系统配置检查 key/余额）
  const hasAiConfigIssue = useMemo(
    () => failureItems.some((item) => isAiConfigError(item.reason)),
    [failureItems]
  );

  // 实时处理进度：processed_count / total_count，与后端轮询结果同步刷新
  const processingStatuses = ['queued', 'running', 'paused'];
  const batchProcessing = batch ? processingStatuses.includes(batch.task.status) : false;
  const batchTotal = batch?.total_count || 0;
  const batchProcessed = batch?.processed_count ?? 0;
  const progress = batchTotal > 0
    ? Math.min(100, Math.max(0, Math.round((batchProcessed / batchTotal) * 100)))
    : 0;

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const loadHistory = async (p?: number, dateFrom?: string, dateTo?: string) => {
    const page = p || historyPage;
    const params = new URLSearchParams();
    params.set('limit', String(HISTORY_PAGE_SIZE));
    params.set('offset', String((page - 1) * HISTORY_PAGE_SIZE));
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    try {
      const data = await ppRequest<{ tasks: TaskHistoryItem[]; total?: number }>(
        ctx, `${API_BASE}/tasks/history?${params.toString()}`
      );
      setHistory(data.tasks || []);
      if (data.total !== undefined) setHistoryTotal(data.total);
    } catch (err) { fail(err); }
  };

  const loadTask = async (taskId: number) => {
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${taskId}/outputs`);
      setBatch(data);
    } catch (err) { fail(err); }
  };

  useEffect(() => {
    loadHistory(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!batch) return;
    const running = ['queued', 'running', 'paused'];
    if (!running.includes(batch.task.status)) return;
    const timer = setInterval(async () => {
      try {
        const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/outputs`);
        setBatch(data);
        if (!running.includes(data.task.status)) { clearInterval(timer); loadHistory(1); }
      } catch { clearInterval(timer); }
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.task_id, batch?.task.status]);

  const startBatch = async () => {
    if (!initialDraftIds?.length) { setError('没有可处理的草稿'); return; }
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: {
          title: '产品处理任务',
          draft_ids: initialDraftIds.slice(0, options.maxProducts || 100),
          max_products: options.maxProducts,
          async_mode: true,
          target_site: options.targetSite,
          target_language: options.targetLanguage,
          processing_scope: options.processingScope,
          qualification_mode: options.qualificationMode,
          include_product_video: options.includeProductVideo,
          skip_duplicates: options.skipDuplicates,
          ip_check: options.ipCheck,
          max_parallel_drafts: options.maxParallelDrafts,
          image_template: options.imageTemplate || 'A',
          image_generation_count: options.imageGenerationCount ?? 1,
        },
      });
      setBatch(data);
      notify(data.message || '批次已提交');
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const clearBatch = async () => {
    if (!batch) return;
    try {
      await ppRequest(ctx, `${API_BASE}/tasks/${batch.task_id}/clear`, { body: {} });
      setBatch(null); loadHistory(1); notify('已清空当前任务');
    } catch (err) { fail(err); }
  };

  const download = (kind: string, filename: string) => {
    if (!batch) return;
    ppDownload(ctx, `${API_BASE}/tasks/${batch.task_id}/download?kind=${kind}`, filename).catch(fail);
  };

  // 重新处理失败/待确认项：以这些草稿为新的批次重新走处理流水线（async_mode 后台执行）
  const retryFailed = async (draftIds: number[]) => {
    if (!draftIds.length) return;
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: {
          title: '失败项重新处理',
          draft_ids: draftIds,
          max_products: 0,
          async_mode: true,
          target_site: options.targetSite,
          target_language: options.targetLanguage,
          processing_scope: options.processingScope,
          qualification_mode: options.qualificationMode,
          include_product_video: options.includeProductVideo,
          skip_duplicates: false,
          ip_check: options.ipCheck,
          max_parallel_drafts: options.maxParallelDrafts,
          image_template: options.imageTemplate || 'A',
          image_generation_count: options.imageGenerationCount ?? 1,
        },
      });
      setBatch(data);
      notify(data.message || `已提交 ${draftIds.length} 个失败项重新处理`);
      loadHistory(1);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  const historyTotalPages = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE));

  return (
    <div className="verify-page">
      {batchProcessing && (
        <div className="daily-topbar-status is-progress" role="status" aria-label="处理进度">
          <span className="daily-topbar-status-icon" aria-hidden="true">↻</span>
          <strong>正在处理</strong>
          <div className="topbar-collection-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <b>{progress}%</b>
        </div>
      )}
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <span className="verify-eyebrow">PRODUCT PROCESSING · 处理任务</span>
          <h1>处理设置与进度</h1>
          <p>配置处理参数后开始，结果实时轮询；已对齐 five-stage 流水线（文本合并一次调用 + 尺寸确定性提取 + 详情图本地合成 + AI 阶段缓存），典型单商品 AI 调用 2~3 次；右侧查看历史任务记录。</p>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      <div className="task-layout">
        {/* 左侧：处理设置 + 处理结果 */}
        <div className="task-main">

          <section className="verify-section">
            <div className="verify-section-head">
              <h2>处理设置</h2>
              <span className="verify-sub">站点 / 语言 / 范围 / 数量</span>
            </div>
            <div className="verify-form-row">
              <label>站点
                <select value={options.targetSite} onChange={(e) => {
                  const site = e.target.value as ProductProcessingOptions['targetSite'];
                  setOptions((p) => ({ ...p, targetSite: site, targetLanguage: site === 'US' ? 'en' : 'es' }));
                }}>
                  {SITES.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
                </select>
              </label>
              <label>语言
                <select value={options.targetLanguage} onChange={(e) => setOptions((p) => ({ ...p, targetLanguage: e.target.value as any }))}>
                  <option value="en">英语 · English</option>
                  <option value="es">西班牙语 · Espanol</option>
                </select>
              </label>
              <label>资质模式
                <select value={options.qualificationMode} onChange={(e) => setOptions((p) => ({ ...p, qualificationMode: e.target.value as any }))}>
                  <option value="standard">标准</option>
                  <option value="strict">严格</option>
                </select>
              </label>
            </div>
            <div className="verify-scope-row">
              <span className="verify-scope-label">处理范围：</span>
              {SCOPES.map((scope) => (
                <label key={scope.key} className="verify-scope-check">
                  <input
                    type="checkbox"
                    checked={options.processingScope.includes(scope.key)}
                    onChange={() => setOptions((p) => {
                      const next = new Set(p.processingScope);
                      if (next.has(scope.key)) next.delete(scope.key); else next.add(scope.key);
                      return { ...p, processingScope: Array.from(next) };
                    })}
                  />{scope.label}
                </label>
              ))}
              <label className="verify-scope-check"><input type="checkbox" checked={options.includeProductVideo} onChange={(e) => setOptions((p) => ({ ...p, includeProductVideo: e.target.checked }))} />生成商品视频</label>
              <label className="verify-scope-check"><input type="checkbox" checked={options.skipDuplicates} onChange={(e) => setOptions((p) => ({ ...p, skipDuplicates: e.target.checked }))} />跳过已处理</label>
              <label className="verify-scope-check"><input type="checkbox" checked={options.ipCheck} onChange={(e) => setOptions((p) => ({ ...p, ipCheck: e.target.checked }))} />侵权词过滤</label>
            </div>
            <div className="verify-form-row">
              <span className="verify-scope-label">生图模板：</span>
              {IMAGE_TEMPLATES.map((template) => (
                <label key={template.id} className="verify-template-card" title={template.description}>
                  <input
                    type="radio"
                    name="image-template"
                    checked={(options.imageTemplate || 'A') === template.id}
                    onChange={() => setOptions((p) => ({ ...p, imageTemplate: template.id }))}
                  />
                  <span className="verify-template-name">{template.name}</span>
                  <span className="verify-template-desc">{template.description}</span>
                </label>
              ))}
            </div>
            <div className="verify-form-row">
              <span className="verify-scope-label">单次生图：</span>
              {IMAGE_GENERATION_OPTIONS.map((option) => (
                <label key={option.count} className="verify-template-card" title={option.description}>
                  <input
                    type="radio"
                    name="image-generation-count"
                    checked={(options.imageGenerationCount ?? 1) === option.count}
                    onChange={() => setOptions((p) => ({ ...p, imageGenerationCount: option.count }))}
                  />
                  <span className="verify-template-name">{option.name}</span>
                  <span className="verify-template-desc">{option.description}</span>
                </label>
              ))}
            </div>
            <div className="verify-slider-row">
              <span className="verify-scope-label">处理数量：</span>
              <input className="verify-slider" type="range" min={0} max={100} step={1} value={options.maxProducts} onChange={(e) => setOptions((p) => ({ ...p, maxProducts: Number(e.target.value) || 0 }))} />
              <span className="verify-slider-value">{options.maxProducts === 0 ? `全部 (${initialDraftIds?.length || 0}项)` : options.maxProducts}</span>
            </div>
            <div className="verify-slider-row">
              <span className="verify-scope-label">最大并行：</span>
              <input className="verify-slider" type="range" min={1} max={20} step={1} value={options.maxParallelDrafts} onChange={(e) => setOptions((p) => ({ ...p, maxParallelDrafts: Number(e.target.value) || 1 }))} />
              <span className="verify-slider-value">{options.maxParallelDrafts} 线程{options.maxParallelDrafts <= 1 ? '（串行）' : ''}</span>
            </div>
            <div className="verify-actions">
              <button className="primary" onClick={() => startBatch()} disabled={loading || !initialDraftIds?.length}>{loading ? '处理中...' : '开始处理'}</button>
              <button onClick={clearBatch} disabled={!batch}>清空任务</button>
            </div>
          </section>

          <section className="verify-section">
            <div className="verify-section-head">
              <h2>处理结果</h2>
              <span className="verify-sub">{batch ? taskStatusLabel(batch.task.status) : '暂无任务'}</span>
            </div>
            {batch && (
              <>
                {batch.total_count > 0 && (
                  <div className="verify-progress-area">
                    <div
                      className={`verify-progress ${batchProcessing ? 'is-live' : 'is-done'}`}
                      role="progressbar"
                      aria-label="处理进度"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={progress}
                    >
                      <svg viewBox="0 0 46 46" aria-hidden="true">
                        <circle className="verify-progress-track" cx="23" cy="23" r="18" pathLength="100" />
                        <circle className="verify-progress-value" cx="23" cy="23" r="18" pathLength="100" strokeDashoffset={100 - progress} />
                      </svg>
                      <strong>{progress}%</strong>
                    </div>
                    <div className="verify-progress-meta">
                      <strong>{batchProcessing ? '正在处理…' : taskStatusLabel(batch.task.status)}</strong>
                      <span>
                        已处理 <b>{batchProcessed}</b> / {batchTotal} 条 · 成功 {batch.success_count} · 失败 {batch.failed_count}
                      </span>
                      <span className="verify-elapsed">已用时 {formatDuration(batch.elapsed_seconds ?? batch.task.elapsed_seconds)}</span>
                    </div>
                  </div>
                )}
                <div className="verify-summary">
                  <div className="verify-count">总数 <b>{batch.total_count}</b></div>
                  <div className="verify-count success">成功 <b>{batch.success_count}</b></div>
                  <div className="verify-count failed">失败 <b>{batch.failed_count}</b></div>
                  <div className="verify-count">跳过 <b>{batch.skipped_count}</b></div>
                  <div className="verify-count">待确认 <b>{batch.attention_required_count}</b></div>
                  <div className="verify-count">技术可重试 <b>{batch.technical_retryable_count}</b></div>
                  <div className="verify-count">配置阻断 <b>{batch.configuration_blocked_count}</b></div>
                </div>
                <div className="verify-actions">
                  <button
                    disabled={batchProcessing}
                    onClick={() => download('dxm', `dxm_import_task_${batch.task_id}.xlsx`)}
                    title={batchProcessing ? '处理完成后可下载' : undefined}
                  >下载店小秘导入表</button>
                  <button
                    disabled={batchProcessing}
                    onClick={() => download('errors', `error_report_task_${batch.task_id}.csv`)}
                    title={batchProcessing ? '处理完成后可下载' : undefined}
                  >下载失败原因表</button>
                  <button
                    disabled={batchProcessing}
                    onClick={() => download('video_manifest', `product_video_manifest_task_${batch.task_id}.csv`)}
                    title={batchProcessing ? '处理完成后可下载' : undefined}
                  >下载视频清单</button>
                  <button
                    className="primary"
                    disabled={batchProcessing}
                    onClick={() => onOpenPrecheck?.(batch.task_id)}
                    title={batchProcessing ? '处理完成后可进入预检' : '打开预检页：核对标题/图片/字段，修改后导出最终版表格'}
                  >预检并导出最终版</button>
                </div>
                {batchProcessing && (
                  <p className="verify-download-hint">输出文件将在处理完成后生成，请稍候。</p>
                )}
              </>
            )}
            {!batch && <p className="verify-empty">尚未提交处理批次。请配置参数后点击"开始处理"。</p>}
          </section>

          {failureItems.length > 0 && (
            <section className="verify-section">
              <div className="verify-section-head">
                <h2>失败商品</h2>
                <span className="verify-actions">
                  <button
                    className="btn-mini primary"
                    disabled={loading || batchProcessing}
                    onClick={() => retryFailed(failureItems.map((item) => item.product_draft_id).filter((id): id is number => id != null))}
                    title={batchProcessing ? '当前批次处理中，完成后可重试' : undefined}
                  ><i className="iconfont icon-rocket" aria-hidden="true" />重试全部失败（{failureItems.length}）</button>
                </span>
              </div>
              {hasAiConfigIssue && (
                <p className="verify-ai-config-hint"><i className="iconfont icon-infomation" aria-hidden="true" />{AI_CONFIG_HINT}</p>
              )}
              <table className="verify-table">
                <thead><tr><th>SKC</th><th>标题</th><th>状态</th><th>失败类型</th><th>原因</th><th>操作提示</th><th>可重试</th><th>操作</th></tr></thead>
                <tbody>
                  {failureItems.map((item) => {
                    const result = (item.result as any) || {};
                    const failureClass = result.failure_class || 'unknown';
                    const draftId = item.product_draft_id;
                    return (
                      <tr key={item.id}>
                        <td>{item.skc || '-'}</td><td>{item.title || '-'}</td>
                        <td>{item.status === 'attention_required' ? '待确认' : '失败'}</td>
                        <td>{FAILURE_CLASS_LABELS[failureClass] || failureClass}</td>
                        <td>{isAiConfigError(item.reason) ? 'AI 服务鉴权/额度问题（详见上方提示）' : (item.reason || '-')}</td>
                        <td>{result.operator_hint || '-'}</td>
                        <td>{result.retryable ? '是' : '否'}</td>
                        <td>
                          {draftId != null ? (
                            <button
                              className="btn-mini primary"
                              disabled={loading || batchProcessing}
                              onClick={() => retryFailed([draftId])}
                              title="以该草稿重新提交处理流水线"
                            >重新处理</button>
                          ) : ('-')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}
        </div>

        {/* 右侧：历史任务 */}
        <aside className="task-history">
          <div className="verify-section-head">
            <h2>历史任务</h2>
          </div>
          <div className="task-history-filters">
            <label>从
              <input type="date" value={historyDateFrom} onChange={(e) => setHistoryDateFrom(e.target.value)} />
            </label>
            <label>至
              <input type="date" value={historyDateTo} onChange={(e) => setHistoryDateTo(e.target.value)} />
            </label>
            <button className="btn-mini" onClick={() => { setHistoryPage(1); loadHistory(1, historyDateFrom, historyDateTo); }}>筛选</button>
            <button className="btn-mini" onClick={() => { setHistoryDateFrom(''); setHistoryDateTo(''); setHistoryPage(1); loadHistory(1, '', ''); }}>清除</button>
          </div>
          {history.length === 0 && <p className="verify-empty">暂无历史任务</p>}
          <ul className="verify-history">
            {history.map((task) => (
              <li key={task.task_id}>
                <span className="verify-history-title">{task.title}</span>
                <span className="verify-badge">{taskStatusLabel(task.status)}</span>
                <span className="verify-history-counts">{task.total_count}/{task.success_count}/{task.failed_count}</span>
                {task.elapsed_seconds !== undefined && <span className="verify-history-elapsed">耗时 {formatDuration(task.elapsed_seconds)}</span>}
                <span className="verify-history-date">{new Date(task.created_at).toLocaleString('zh-CN')}</span>
                <button onClick={() => loadTask(task.task_id)}>查看</button>
              </li>
            ))}
          </ul>
          {historyTotalPages > 1 && (
            <footer className="verify-pagination task-history-pagination">
              <button type="button" onClick={() => { const p = Math.max(1, historyPage - 1); setHistoryPage(p); loadHistory(p, historyDateFrom, historyDateTo); }} disabled={historyPage <= 1}>上一页</button>
              <span>第 {historyPage}/{historyTotalPages} 页</span>
              <button type="button" onClick={() => { const p = Math.min(historyTotalPages, historyPage + 1); setHistoryPage(p); loadHistory(p, historyDateFrom, historyDateTo); }} disabled={historyPage >= historyTotalPages}>下一页</button>
            </footer>
          )}
        </aside>
      </div>
    </div>
  );
}

export default ProductProcessingTaskPage;
