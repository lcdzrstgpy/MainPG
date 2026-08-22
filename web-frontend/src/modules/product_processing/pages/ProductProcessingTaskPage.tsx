import { useEffect, useMemo, useRef, useState } from 'react';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import { PromptCustomizePanel } from '../components/PromptCustomizePanel';
import type {
  ProductProcessingOptions,
  TaskOutputsResponse,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';

const SITES = [
  { code: 'US', label: '美国站' },
  { code: 'CO', label: '哥伦比亚站' },
  { code: 'EC', label: '厄瓜多尔站' },
] as const;

// 生图提示词模板（对齐后端 IMAGE_TEMPLATES：A/B 直观标题区分两套生图逻辑）
const IMAGE_TEMPLATES: { id: 'A' | 'B'; name: string; description: string }[] = [
  { id: 'A', name: '标准商品海报', description: 'Amazon 高级电商视觉：大主体主图 + 精品展示 + 生活方式场景，画面不新增文字' },
  { id: 'B', name: '高端模特视觉（防比价）', description: '人设+空间故事叙事、杂志编辑大片感，材质显贵、难以搜图比价，画面无文字' },
];

const FAILURE_CLASS_LABELS: Record<string, string> = {
  technical_retryable: '技术失败可重试',
  configuration_blocked: '配置阻断',
  identity_review_required: '身份待复核',
  logistics_review_required: '尺寸待复核',
};

// AI 配置/额度类失败（401 key 无效、403 权限、402/429 额度或限流、key 未配置等），
// 用户需要去「系统配置」检查 AI key 或账户余额，而不是修改商品数据。
const AI_CONFIG_ERROR_RE =
  /HTTP\s+40[1239]|api\s+key\s+is\s+not\s+configured|insufficient.*(?:quota|balance)|quota|balance|unauthorized|forbidden|invalid.*(?:api\s*key|credentials)/i;

const REMOTE_SERVICE_ERROR_RE =
  /remote billing service is unavailable|provider is temporarily unreachable/i;

function isRemoteServiceError(reason?: string): boolean {
  if (!reason) return false;
  return REMOTE_SERVICE_ERROR_RE.test(reason);
}

function isAiConfigError(reason?: string): boolean {
  if (!reason) return false;
  return AI_CONFIG_ERROR_RE.test(reason);
}

const AI_CONFIG_HINT =
  '似乎 api key 配置有问题哦，可以先去系统配置保存一下或者检查一下余额亲~（当前失败为 AI 服务鉴权/额度问题，与商品数据无关）';

const REMOTE_SERVICE_HINT =
  '服务器计费或 AI 服务暂时不可用，请稍后重试；这不是商品数据或本地 API Key 配置问题。';

type Props = {
  /** 从历史记录重新打开时传入，任务会从服务端恢复并在处理中继续轮询。 */
  initialTaskId?: number;
  initialDraftIds?: number[];
  /** 精品模式草稿：一次 4K 智能生图，本地拆成四张高清独立图 */
  initialPremiumDraftIds?: number[];
  initialOptions?: ProductProcessingOptions;
  /** 任务完成后打开预检页（生成表格 → 预检修改 → 导出最终版） */
  onOpenPrecheck?: (taskId: number) => void;
};

function api(): ApiContext {
  return productProcessingApiContext();
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

export function ProductProcessingTaskPage({ initialTaskId, initialDraftIds, initialPremiumDraftIds, initialOptions, onOpenPrecheck }: Props) {
  const ctx = api();
  // 处理参数全部为系统默认（范围全开、全部数量、8 线程并行、自动补跑等），
  // 精简设置界面后不再向用户暴露这些项；仅站点 / 语言 / 生图模板可选。
  const [options, setOptions] = useState<ProductProcessingOptions>(() =>
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
      autoRepull: true,
    }
  );
  const [batch, setBatch] = useState<TaskOutputsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [controlBusy, setControlBusy] = useState(false);
  const startInFlightRef = useRef(false);
  const startRequestRef = useRef<{ signature: string; key: string } | null>(null);
  const pollGenerationRef = useRef(0);
  // 失败商品明细默认折叠：自动补跑已覆盖大部分缺陷项，避免把错误明细
  // 直接摊在结果页上（展开才可见并可手动重试）。
  const [showFailures, setShowFailures] = useState(false);

  // 失败项自动补跑（后台自动重处理）状态
  const autoRepull = batch?.auto_repull ?? null;
  const autoRepullRunning = autoRepull?.status === 'running';
  const autoRepullDone = autoRepull?.status === 'completed';
  const autoRepullFailed = autoRepull?.status === 'failed';

  const failureItems = useMemo(
    () => batch?.items.filter(
      (item) => item.status === 'attention_required' || item.status === 'failed'
    ) || [],
    [batch]
  );

  // 身份待复核（AI 无法确认可售主体）项：批量重试大概率无效，需人工确认主体可售后放行
  const identityFailureItems = useMemo(
    () => failureItems.filter((item) => ((item.result as any)?.error_type) === 'vision_subject_low_confidence'),
    [failureItems]
  );
  const identityDraftIdSet = useMemo(
    () => new Set(identityFailureItems.map((item) => item.product_draft_id)),
    [identityFailureItems]
  );
  // 真正的技术失败（含图片质量、AI 服务等）：可批量重试 / 知晓入库
  const technicalFailureItems = useMemo(
    () => failureItems.filter((item) => !identityDraftIdSet.has(item.product_draft_id)),
    [failureItems, identityDraftIdSet]
  );

  // 是否存在 AI 配置/额度类失败（此时应提示用户去系统配置检查 key/余额）
  const hasAiConfigIssue = useMemo(
    () => failureItems.some(
      (item) => !isRemoteServiceError(item.reason) && isAiConfigError(item.reason)
    ),
    [failureItems]
  );
  const hasRemoteServiceIssue = useMemo(
    () => failureItems.some((item) => isRemoteServiceError(item.reason)),
    [failureItems]
  );

  // 实时处理进度：processed_count / total_count，与后端轮询结果同步刷新。
  // 自动补跑轮（含轮间切换的短暂窗口）仍视为「处理中」：处理过程中不展示
  // 失败信息，进度条在全部轮次（含最多 2 轮自动重试）跑完前不显示 100%。
  // 暂停单独识别（含重试轮中的暂停），给用户明确的「已暂停」反馈。
  const processingStatuses = ['queued', 'running'];
  const batchProcessing = batch
    ? (processingStatuses.includes(batch.task.status) || autoRepullRunning)
    : false;
  const taskPaused = batch?.task.status === 'paused';
  const taskActive = batchProcessing || taskPaused;
  const batchTotal = batch?.total_count || 0;
  const batchProcessed = batch?.processed_count ?? 0;
  const taskDone = batch
    ? ['completed', 'partial_failure'].includes(batch.task.status) && !autoRepullRunning
    : false;
  const progress = taskDone
    ? 100
    : batchTotal > 0
      ? Math.min(99, Math.max(0, Math.round((batchProcessed / batchTotal) * 100)))
      : 0;
  // 本轮自动重试进度：轮内已处理条数 = 轮总数 - 仍处于 pending/running 的条数
  // （重试轮只有本轮失败的链接会被重置为 pending，其余已成功项保持 completed）。
  const roundRemaining = batch
    ? batch.items.filter((item) => item.status === 'pending' || item.status === 'running').length
    : 0;
  const roundDone = autoRepullRunning && autoRepull
    ? Math.max(0, Math.min(autoRepull.total, autoRepull.total - roundRemaining))
    : 0;

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const loadTask = async (taskId: number) => {
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${taskId}/outputs`);
      setBatch(data);
    } catch (err) { fail(err); }
  };

  useEffect(() => {
    if (initialTaskId != null) void loadTask(initialTaskId);
    // initialTaskId only changes when WorkspaceShell opens another task tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTaskId]);

  const downloadOutput = async (kind: 'dxm' | 'errors' | 'video_manifest', filename: string) => {
    if (!batch) return;
    try {
      await ppDownload(ctx, `${API_BASE}/tasks/${batch.task_id}/download?kind=${kind}`, filename);
    } catch (err) { fail(err); }
  };

  useEffect(() => {
    if (!batch) return;
    const running = ['queued', 'running', 'paused'];
    // 自动补跑轮（含轮间 task 状态短暂回到 completed 的窗口）必须继续轮询，
    // 否则重试轮的进度与耗时更新会永远停在第 1 轮结束前。
    const active = running.includes(batch.task.status) || batch.auto_repull?.status === 'running';
    if (!active) return;
    const generation = ++pollGenerationRef.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    const poll = async () => {
      try {
        const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/outputs`);
        if (stopped || pollGenerationRef.current !== generation) return;
        setBatch(data);
        const stillActive = running.includes(data.task.status) || data.auto_repull?.status === 'running';
        if (!stillActive) {
          return;
        }
      } catch (err) {
        if (stopped || pollGenerationRef.current !== generation) return;
        fail(err);
      }
      timer = setTimeout(poll, 1000);
    };
    timer = setTimeout(poll, 1000);
    return () => {
      stopped = true;
      pollGenerationRef.current += 1;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.task_id, batch?.task.status, batch?.auto_repull?.status]);

  const startBatch = async () => {
    if (!initialDraftIds?.length) { setError('没有可处理的草稿'); return; }
    if (startInFlightRef.current || batchProcessing) return;
    const body = {
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
      // 用户选择是否对技术可重试的失败项自动补跑（默认开启）
      auto_repull: options.autoRepull !== false,
      // 兼容旧 API 字段；新任务统一走智能生图策略，不再由用户选择。
      image_generation_count: 4,
    };
    const signature = JSON.stringify(body);
    if (!startRequestRef.current || startRequestRef.current.signature !== signature) {
      startRequestRef.current = {
        signature,
        key: globalThis.crypto?.randomUUID?.() || `pp-${Date.now()}-${Math.random()}`,
      };
    }
    startInFlightRef.current = true;
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const premiumIds = (initialPremiumDraftIds || []).filter((id) => initialDraftIds.includes(id));
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: { ...body, premium_draft_ids: premiumIds },
        headers: { 'Idempotency-Key': startRequestRef.current.key },
      });
      setBatch(data);
      notify(premiumIds.length ? data.message || `批次已提交（含 ${premiumIds.length} 条精品单图）` : (data.message || '批次已提交'));
    } catch (err) { fail(err); } finally { startInFlightRef.current = false; setLoading(false); }
  };

  const clearBatch = async () => {
    if (!batch || batchProcessing) return;
    try {
      await ppRequest(ctx, `${API_BASE}/tasks/${batch.task_id}/clear`, { body: {} });
      setBatch(null); notify('已清空当前任务');
    } catch (err) { fail(err); }
  };

  // 暂停：协作式停止——当前正在处理的商品跑完后不再启动新商品（不打断进行中的 AI 调用）。
  const pauseTask = async () => {
    if (!batch || controlBusy) return;
    setControlBusy(true);
    try {
      const data = await ppRequest(ctx, `${API_BASE}/tasks/${batch.task_id}/pause`, { body: {} });
      notify((data as { message?: string }).message || '任务已暂停，当前商品完成后停止');
    } catch (err) { fail(err); }
    finally { setControlBusy(false); }
  };

  // 继续：暂停后从剩余商品继续处理（已完成的商品自动跳过）。
  const resumeTask = async () => {
    if (!batch || controlBusy) return;
    setControlBusy(true);
    try {
      const data = await ppRequest(ctx, `${API_BASE}/tasks/${batch.task_id}/resume`, { body: {} });
      notify((data as { message?: string }).message || '任务已继续处理');
    } catch (err) { fail(err); }
    finally { setControlBusy(false); }
  };

  // 重新处理失败/待确认项：以这些草稿为新的批次重新走处理流水线（async_mode 后台执行）。
  // 手动重试 = 付费重试：无论最终成功或失败都按 35-45 积分/条计费，提交前需确认。
  const retryFailed = async (draftIds: number[]) => {
    if (!draftIds.length || !batch || batchProcessing || startInFlightRef.current) return;
    if (!window.confirm(`重新处理 ${draftIds.length} 条失败链接将按单条 35-45 积分计费，无论最终成功或失败均会扣费，确认继续？`)) return;
    startInFlightRef.current = true;
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/retry-attention`, {
        body: { draft_ids: draftIds, auto_repull: options.autoRepull !== false },
      });
      setBatch(data);
      notify(data.message || `已提交 ${draftIds.length} 个失败项重新处理`);
    } catch (err) { fail(err); } finally { startInFlightRef.current = false; setLoading(false); }
  };

  // 强制入库：用户「我已知晓，仍要入库」——图片质量门不再阻断，回退来源图继续走完流水线，
  // 预审环节可人工修正图片/信息
  const forceImportFailed = async (draftIds: number[]) => {
    if (!draftIds.length) return;
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: {
          title: '失败项强制入库',
          draft_ids: draftIds,
          force_import_draft_ids: draftIds,
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
          // 强制入库是用户明确放行，不再触发失败项自动补跑
          auto_repull: false,
        },
      });
      setBatch(data);
      notify(data.message || `已提交 ${draftIds.length} 个失败项强制入库`);
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  // 确认主体可售：跳过主体识别门，继续文案与生图直至入库（身份待复核专用）
  const confirmIdentitySellable = async (draftIds: number[]) => {
    if (!draftIds.length || !batch || batchProcessing || startInFlightRef.current) return;
    startInFlightRef.current = true;
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/identity-confirm`, {
        body: { draft_ids: draftIds },
      });
      setBatch(data);
      notify(data.message || `已确认 ${draftIds.length} 个商品主体可售，继续处理`);
    } catch (err) { fail(err); } finally { startInFlightRef.current = false; setLoading(false); }
  };

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
          <h1>{initialTaskId != null ? '历史任务详情' : '处理设置与进度'}</h1>
          <p>{initialTaskId != null ? '继续处理中的历史任务；关闭本页不会停止后台任务，可从左侧“历史记录”再次打开。' : '配置处理参数后开始，结果实时轮询。'}</p>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      <div className="task-layout">
        {/* 左侧：处理设置 + 处理结果 */}
        <div className="task-main">

          {initialTaskId == null && <section className="verify-section">
            <div className="verify-section-head">
              <h2>处理设置</h2>
              <span className="verify-sub">站点 / 语言 / 生图模板</span>
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
            <PromptCustomizePanel />
            <div className="verify-actions">
              <button className="primary" onClick={() => startBatch()} disabled={loading || batchProcessing || !initialDraftIds?.length}>{loading ? '处理中...' : '开始处理'}</button>
              <button onClick={clearBatch} disabled={!batch || batchProcessing} title={batchProcessing ? '运行中任务不能清理' : undefined}>清空任务</button>
              {!!initialPremiumDraftIds?.length && (
                <span className="verify-premium-hint">精品模式 {initialPremiumDraftIds.length} 条：一次 4K 智能生图，拆为 4 张高清图</span>
              )}
            </div>
          </section>}

          <section className="verify-section">
            <div className="verify-section-head">
              <h2>处理结果</h2>
              <span className="verify-sub">{batch ? (taskActive ? (taskPaused ? '已暂停' : '处理中') : taskStatusLabel(batch.task.status)) : '暂无任务'}</span>
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
                      <strong>{taskPaused
                        ? '已暂停'
                        : batchProcessing
                          ? (autoRepullRunning ? (autoRepull?.message || '重试波动链接中…') : '正在处理…')
                          : taskStatusLabel(batch.task.status)}</strong>
                      <span>
                        {autoRepullRunning && autoRepull
                          ? <>本轮自动重试 <b>{roundDone}</b> / {autoRepull.total} 条</>
                          : <>已处理 <b>{batchProcessed}</b> / {batchTotal} 条</>}
                      </span>
                      <span className="verify-elapsed">已用时 {formatDuration(batch.elapsed_seconds ?? batch.task.elapsed_seconds)}</span>
                    </div>
                  </div>
                )}
                {/* 结果汇总仅在全部处理（含自动重试轮）完成后展示，处理中/已暂停不暴露失败统计 */}
                {!taskActive && (
                <div className="verify-summary">
                  <div className="verify-count">总数 <b>{batch.total_count}</b></div>
                  <div className="verify-count success">成功 <b>{batch.success_count}</b></div>
                  <div className="verify-count failed">失败 <b>{batch.failed_count}</b></div>
                  <div className="verify-count">跳过 <b>{batch.skipped_count}</b></div>
                  <div className="verify-count">待确认 <b>{batch.attention_required_count}</b></div>
                  <div className="verify-count">技术可重试 <b>{batch.technical_retryable_count}</b></div>
                  <div className="verify-count">配置阻断 <b>{batch.configuration_blocked_count}</b></div>
                </div>
                )}
                {(taskPaused || autoRepullRunning || autoRepullDone || autoRepullFailed) && (taskPaused || autoRepull) && (
                  <div className={`verify-repull-banner ${taskPaused ? 'paused' : (autoRepull?.status ?? '')}`} role="status">
                    <i className={`iconfont ${taskPaused ? 'icon-infomation' : autoRepullRunning ? 'icon-loading' : autoRepullDone ? 'icon-check-circle' : 'icon-infomation'}`} aria-hidden="true" />
                    <span>{taskPaused ? '任务已暂停：当前商品完成后将停止，可从断点继续（含自动重试轮）' : (autoRepull?.message ?? '')}</span>
                    {!taskPaused && autoRepullRunning && <em>无需手动操作，完成后自动刷新</em>}
                  </div>
                )}
                <div className="verify-actions">
                  {(batchProcessing || taskPaused) && (
                    <button
                      className={batch.task.status === 'paused' ? 'primary' : ''}
                      disabled={controlBusy || loading}
                      onClick={() => void (batch.task.status === 'paused' ? resumeTask() : pauseTask())}
                      title={batch.task.status === 'paused' ? '继续处理剩余商品' : '暂停处理：当前商品完成后停止，不再启动新商品'}
                    >{batch.task.status === 'paused' ? '继续处理' : '暂停处理'}</button>
                  )}
                  <button
                    className="primary"
                    disabled={taskActive}
                    onClick={() => onOpenPrecheck?.(batch.task_id)}
                    title={taskActive ? '处理完成后可进入预检' : '打开预检页：核对标题/图片/字段，修改后导出最终版表格'}
                  >预检并导出最终版</button>
                  {batch.outputs.dxm_import && <button onClick={() => void downloadOutput('dxm', `dxm_import_task_${batch.task_id}.xlsx`)}>下载导入表</button>}
                  {batch.outputs.error_report && <button onClick={() => void downloadOutput('errors', `error_report_task_${batch.task_id}.csv`)}>下载错误报告</button>}
                  {batch.outputs.product_video_manifest && <button onClick={() => void downloadOutput('video_manifest', `product_video_manifest_task_${batch.task_id}.csv`)}>下载视频清单</button>}
                </div>
              </>
            )}
            {!batch && <p className="verify-empty">尚未提交处理批次。请配置参数后点击"开始处理"。</p>}
          </section>

          {/* 失败明细只在全部处理（含 2 轮自动重试）跑完后展示最终失败链接及原因 */}
          {failureItems.length > 0 && !taskActive && (
            <section className="verify-section">
              <div className="verify-section-head">
                <h2>失败商品 <span className="verify-failure-count">（{failureItems.length} 项）</span></h2>
                <span className="verify-actions">
                  <button className="btn-mini" onClick={() => setShowFailures((v) => !v)} title={showFailures ? '收起失败明细' : '展开失败明细，可手动重新处理或知晓入库'}>{showFailures ? '收起明细' : '查看明细'}</button>
                  {showFailures && (
                    <>
                  {technicalFailureItems.length > 0 && (
                    <button
                      className="btn-mini primary"
                      disabled={loading || batchProcessing}
                      onClick={() => retryFailed(technicalFailureItems.map((item) => item.product_draft_id).filter((id): id is number => id != null))}
                      title={batchProcessing ? '当前批次处理中，完成后可重试' : '仅重试技术失败项（图片质量、AI 服务等）；身份待复核项请用「确认主体可售」'}
                    ><i className="iconfont icon-rocket" aria-hidden="true" />重试全部失败（{technicalFailureItems.length}）</button>
                  )}
                  {technicalFailureItems.length > 0 && (
                    <button
                      className="btn-mini force-import"
                      disabled={loading || batchProcessing}
                      onClick={() => forceImportFailed(technicalFailureItems.map((item) => item.product_draft_id).filter((id): id is number => id != null))}
                      title={batchProcessing ? '当前批次处理中，完成后可操作' : '我已知道生成图质量问题，仍要提交入库（回退来源图，预审可修改）'}
                    ><i className="iconfont icon-check-circle" aria-hidden="true" />全部知晓入库（{technicalFailureItems.length}）</button>
                  )}
                  {identityFailureItems.length > 0 && (
                    <button
                      className="btn-mini confirm-subject"
                      disabled={loading || batchProcessing}
                      onClick={() => confirmIdentitySellable(identityFailureItems.map((item) => item.product_draft_id).filter((id): id is number => id != null))}
                      title={batchProcessing ? '当前批次处理中，完成后可操作' : '主图存在多个或遮挡主体；确认可售后跳过主体识别门，继续文案与生图直至入库'}
                    ><i className="iconfont icon-check-circle" aria-hidden="true" />全部确认主体可售（{identityFailureItems.length}）</button>
                  )}
                    </>
                  )}
                </span>
              </div>
              {showFailures && (
                <>
                  {hasAiConfigIssue && (
                    <p className="verify-ai-config-hint"><i className="iconfont icon-infomation" aria-hidden="true" />{AI_CONFIG_HINT}</p>
                  )}
                  {hasRemoteServiceIssue && (
                    <p className="verify-ai-config-hint"><i className="iconfont icon-infomation" aria-hidden="true" />{REMOTE_SERVICE_HINT}</p>
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
                            <span className="verify-row-actions">
                              <button
                                className="btn-mini primary"
                                disabled={loading || batchProcessing}
                                onClick={() => retryFailed([draftId])}
                                title="以该草稿重新提交处理流水线"
                              >重新处理</button>
                              {result.error_type === 'vision_subject_low_confidence' ? (
                                <button
                                  className="btn-mini confirm-subject"
                                  disabled={loading || batchProcessing}
                                  onClick={() => confirmIdentitySellable([draftId])}
                                  title="主图存在多个或遮挡主体；确认可售后跳过主体识别门，继续文案与生图直至入库"
                                >确认主体可售</button>
                              ) : (
                                result.retryable && (
                                  <button
                                    className="btn-mini force-import"
                                    disabled={loading || batchProcessing}
                                    onClick={() => forceImportFailed([draftId])}
                                    title="我已知道生成图质量问题，仍要提交入库（回退来源图，预审可修改）"
                                  >我已知晓，仍要入库</button>
                                )
                              )}
                            </span>
                          ) : ('-')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
                </>
              )}
            </section>
          )}
        </div>

      </div>
    </div>
  );
}

export default ProductProcessingTaskPage;
