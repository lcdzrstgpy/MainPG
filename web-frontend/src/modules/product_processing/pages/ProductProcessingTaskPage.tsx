import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import { checkDraftSkuAvailability } from '../api/productProcessingApi';
import { ImageComboPicker } from '../components/ImageComboPicker';
import {
  DEFAULT_PANEL_SELECTIONS,
  SIMPLE_PRESETS,
  type ImageCategoryId,
  type PanelSelection,
  type PhotoLayerKey,
  type PhotoSelection,
} from '../data/imageCardOptions';
import {
  buildComboSnapshot,
  cloneComboPanels,
  loadComboHistory,
  loadLastCombo,
  pushComboHistory,
  saveLastCombo,
  type ComboSnapshot,
} from '../data/imageComboHistory';
import type {
  DraftSkuAvailabilityItem,
  ProductProcessingOptions,
  TaskOutputsResponse,
} from '../types';
import { registerRunningTask } from '../../../shared/runtime/taskActivity';
import '../styles/ProductProcessingVerifyPage.css';

const IMAGE_LAYER_OPTION_KEYS: Record<
  PhotoLayerKey,
  'imageFrame' | 'imageLight' | 'imageTone' | 'imageDetail' | 'imageStyle'
> = {
  frame: 'imageFrame',
  light: 'imageLight',
  tone: 'imageTone',
  detail: 'imageDetail',
  style: 'imageStyle',
};

const API_BASE = '/api/product-processing';

// 任务终态后的慢速追跟窗口：任务行在「第一轮结束 → 自动补跑轮启动」之间会短暂回到
// completed，若此刻立刻停止轮询，就再也看不到后续轮次，心跳也会在补跑轮进行中断掉
// （后端 90 秒后会把正在补跑的任务自动暂停）。这里终态后每 3 秒再看 2 分钟。
const TERMINAL_POLL_GRACE_MS = 120_000;
const TERMINAL_POLL_INTERVAL_MS = 3_000;

// SKU 原图可用性检测按批提交：逐批更新进度，避免一次性几百条把单个请求拖成分钟级。
// 10 条一批（原 20）：每批返回更快，进度条能持续前进；后端仍按 OCR 并发上限并行，
// 批间空档由后端内容哈希缓存 + 同一批内的 asset 去重抵消。
const SKU_CHECK_BATCH_SIZE = 10;

const SITES = [
  { code: 'US', label: '美国站' },
  { code: 'CO', label: '哥伦比亚站' },
  { code: 'EC', label: '厄瓜多尔站' },
] as const;

// 失败类型列对客户统一展示为「已过滤」，不暴露内部排错分类（身份待复核/
// 技术可重试等词只对运营排错有用，客户看到成功/失败/需重试即可）。
const FAILURE_CLASS_LABELS: Record<string, string> = {
  technical_retryable: '已过滤',
  configuration_blocked: '已过滤',
  identity_review_required: '已过滤',
  logistics_review_required: '已过滤',
};

// 失败商品的统一客户提示：系统评判该链接生图做链接有风险，已自动过滤。
const FAILURE_FILTER_HINT = '该条链接系统评判生图做链接有风险，已被过滤';

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
  /** 本批次勾选草稿的 SKU 总数（草稿池提交时统计，用于开始前确认）。 */
  initialSkuCount?: number;
  initialOptions?: ProductProcessingOptions;
  /** 任务完成后打开预检页（生成表格 → 预检修改 → 导出最终版） */
  onOpenPrecheck?: (taskId: number) => void;
  /** 返回草稿池删减 SKU：关闭当前处理页签并切回草稿池。 */
  onBackToDraftPool?: () => void;
  /**
   * 本页签接手了真实任务（首次「开始处理」或重试失败项）时回调，由工作台把 task_id 记到页签上。
   * 刷新恢复后该页签按「任务详情」恢复：继续轮询 /outputs（页面在看 = 不会被暂停），
   * 也不会因为不知道任务已存在而再点一次「开始处理」建出重复任务。
   */
  onTaskStarted?: (taskId: number) => void;
  /**
   * 当前页签是否为激活页签。工作台的页签是常驻挂载（切走只是 hidden），若这里不判断，
   * 隐藏页签仍会每秒拉一次全量结果并整页重渲染：多开 2-3 个处理页签就会把 WebView2
   * 的 CPU/内存吃到卡死，随后本地后端探活超时表现成 failed to fetch。
   */
  isActive?: boolean;
};

function api(): ApiContext {
  return productProcessingApiContext();
}

function taskStatusLabel(status: string): string {
  return ({
    queued: '等待处理',
    running: '处理中',
    paused: '已暂停',
    cancelled: '已取消',
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

/** 进度平滑插值：轮询返回的是整数百分比，直接渲染会一格一格跳。
 *  这里用 requestAnimationFrame 做指数缓动，让圆环与数字始终连续推进。
 *  缓动状态收在独立组件内，逐帧重渲染只影响这个圆环，不拖累整页。 */
function ProgressRing({ progress, running }: { progress: number; running: boolean }) {
  const smooth = useSmoothProgress(progress);
  return (
    <div
      className={`verify-progress-ring ${running ? 'is-live' : 'is-done'}`}
      role="progressbar"
      aria-label="处理进度"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={progress}
    >
      <span className="verify-progress-halo" aria-hidden="true" />
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <defs>
          <linearGradient id="verify-progress-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#2fe0e6" />
            <stop offset="55%" stopColor="#0baec0" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
        </defs>
        <circle className="verify-progress-track" cx="60" cy="60" r="52" pathLength="100" />
        {/* 进度为 0 时用旋转弧表示「正在跑」；一旦有确定进度就只保留进度弧，
            避免旋转弧与常亮进度弧在圆环上叠在一起。 */}
        {running && smooth < 0.5 && (
          <circle className="verify-progress-sweep" cx="60" cy="60" r="52" pathLength="100" aria-hidden="true" />
        )}
        <circle className="verify-progress-value" cx="60" cy="60" r="52" pathLength="100" strokeDashoffset={100 - smooth} />
      </svg>
      <div className="verify-progress-center">
        <strong>{Math.round(smooth)}<em>%</em></strong>
        <span>{running ? '处理中' : '已完成'}</span>
      </div>
    </div>
  );
}

/** 进度平滑插值：把轮询得到的整数进度缓动成连续值。 */
function useSmoothProgress(target: number): number {
  const [value, setValue] = useState(target);
  const valueRef = useRef(target);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const diff = target - valueRef.current;
      if (Math.abs(diff) < 0.05) {
        valueRef.current = target;
        setValue(target);
        return;
      }
      valueRef.current += diff * 0.12;
      setValue(valueRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return value;
}

export function ProductProcessingTaskPage({ initialTaskId, initialDraftIds, initialPremiumDraftIds, initialSkuCount, initialOptions, onOpenPrecheck, onTaskStarted, onBackToDraftPool, isActive = true }: Props) {
  const ctx = api();
  // 处理参数全部为系统默认（范围全开、全部数量、8 线程并行、自动补跑等），
  // 精简设置界面后不再向用户暴露这些项；仅站点 / 语言 / 构图模板 / 批次生图组合可选。
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
      imageCategoryOverride: 'auto',
      imageMode: 'simple',
      imageFrame: 'auto',
      imageLight: 'auto',
      imageTone: 'auto',
      imageDetail: 'auto',
      imageStyle: 'auto',
      imagePanels: DEFAULT_PANEL_SELECTIONS,
      imagePreset: 'auto',
      // 随机模板：默认关闭；开启后本批次每条链接随机分配一个效果模板。
      randomImagePreset: false,
      descriptionTemplate: 'A',
      // 失败项自动重跑默认关闭：重跑会再次调用 AI 生图并计费，交给用户显式开启。
      autoRepull: false,
      // 尺寸图自动烧制默认关闭：烧制会额外占用 AI/本地渲染时间，交给用户按品类显式开启。
      dimensionCard: false,
    }
  );
  const imageSelection: PhotoSelection = {
    frame: options.imageFrame || 'auto',
    light: options.imageLight || 'auto',
    tone: options.imageTone || 'auto',
    detail: options.imageDetail || 'auto',
    style: options.imageStyle || 'auto',
  };

  // 「历史预设」：本地保留最近几次生图组合，供一键回填。
  const [comboHistory, setComboHistory] = useState<ComboSnapshot[]>(() => loadComboHistory());
  const comboHydratedRef = useRef(false);
  const comboPersistReadyRef = useRef(false);

  // 首次进入恢复上一次的生图组合（initialOptions 显式传入时以它为准）。
  useEffect(() => {
    if (comboHydratedRef.current) return;
    comboHydratedRef.current = true;
    if (initialOptions) return;
    const last = loadLastCombo();
    if (!last) return;
    setOptions((p) => ({
      ...p,
      imageMode: last.mode,
      imageCategoryOverride: last.category as ImageCategoryId,
      imageFrame: last.layers.frame,
      imageLight: last.layers.light,
      imageTone: last.layers.tone,
      imageDetail: last.layers.detail,
      imageStyle: last.layers.style,
      imagePanels: cloneComboPanels(last.panels),
      imagePreset: last.presetId,
    }));
  }, [initialOptions]);

  // 每次调整后自动记住当前组合，下次打开直接恢复。
  useEffect(() => {
    if (!comboPersistReadyRef.current) {
      comboPersistReadyRef.current = true;
      return;
    }
    saveLastCombo(
      buildComboSnapshot({
        mode: options.imageMode || 'simple',
        presetId: options.imagePreset || 'auto',
        category: options.imageCategoryOverride || 'auto',
        layers: imageSelection,
        panels: options.imagePanels || DEFAULT_PANEL_SELECTIONS,
      }),
    );
  }, [
    options.imageMode,
    options.imagePreset,
    options.imageCategoryOverride,
    options.imageFrame,
    options.imageLight,
    options.imageTone,
    options.imageDetail,
    options.imageStyle,
    options.imagePanels,
  ]);

  const applyComboSnapshot = (snapshot: ComboSnapshot) => {
    setOptions((p) => ({
      ...p,
      imageMode: snapshot.mode,
      imageCategoryOverride: snapshot.category as ImageCategoryId,
      imageFrame: snapshot.layers.frame,
      imageLight: snapshot.layers.light,
      imageTone: snapshot.layers.tone,
      imageDetail: snapshot.layers.detail,
      imageStyle: snapshot.layers.style,
      imagePanels: cloneComboPanels(snapshot.panels),
      imagePreset: snapshot.presetId,
    }));
    setComboHistory(pushComboHistory({ ...snapshot, savedAt: Date.now() }));
  };

  const applyImageSelection = (
    selection: PhotoSelection,
    panels?: PanelSelection[],
    presetId = 'auto',
  ) => {
    setOptions((p) => ({
      ...p,
      imageFrame: selection.frame,
      imageLight: selection.light,
      imageTone: selection.tone,
      imageDetail: selection.detail,
      imageStyle: selection.style,
      imagePanels: (panels ?? DEFAULT_PANEL_SELECTIONS).map((panel) => ({
        ...panel,
        props: panel.props ? [...panel.props] : null,
      })),
      imagePreset: presetId,
    }));
  };

  const setImageLayer = (layer: PhotoLayerKey, value: string) => {
    const key = IMAGE_LAYER_OPTION_KEYS[layer];
    setOptions((p) => ({ ...p, [key]: value, imagePreset: 'auto' }));
  };

  const [batch, setBatch] = useState<TaskOutputsResponse | null>(null);
  // 打开历史任务详情时，数据回来前显示加载块，避免误显示「尚未提交处理批次」。
  const [taskLoading, setTaskLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [controlBusy, setControlBusy] = useState(false);
  // 开始处理前的批次规模确认：链接/SKU 过多时提醒低配置电脑可能卡顿。
  const [startConfirmOpen, setStartConfirmOpen] = useState(false);
  const startInFlightRef = useRef(false);
  const startRequestRef = useRef<{ signature: string; key: string } | null>(null);
  const pollGenerationRef = useRef(0);
  // 失败商品明细默认折叠：自动补跑已覆盖大部分缺陷项，避免把错误明细
  // 直接摊在结果页上（展开才可见并可手动重试）。
  const [showFailures, setShowFailures] = useState(false);

  // 成功类提示统一走右上角 toast：3 秒后自动消失；新消息会清理旧定时器，卸载时也不留悬挂回调。
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(''), 3000);
    return () => window.clearTimeout(timer);
  }, [message]);

  // SKU 原图可用性检测（处理设置页）：进入即对本次所选链接自动检测，按结果预置每个
  // 链接导出时用「规格原图」还是「商品主图」。结果仅作预置，用户仍可在预检页修改。
  const [skuResults, setSkuResults] = useState<Record<number, DraftSkuAvailabilityItem>>({});
  // 「优化链接 SKU」：勾选后自动删除含中文规格图的链接中对应 SKU（默认开启）。
  const [optimizeSku, setOptimizeSku] = useState(true);
  const [skuCheckBusy, setSkuCheckBusy] = useState(false);
  const [skuCheckDone, setSkuCheckDone] = useState(false);
  const [skuCheckError, setSkuCheckError] = useState('');
  const [skuCheckProgress, setSkuCheckProgress] = useState({ done: 0, total: 0 });
  /** 只判尺寸、跳过中文 OCR：1:1 读元数据零成本，中文检测要逐张跑 OCR（很慢）。 */
  const [skipSkuTextCheck, setSkipSkuTextCheck] = useState(false);
  /** 用户确认跳过检测：本批 SKU 图统一用生成后的主图，不再复用规格原图。 */
  const [skuCheckSkipped, setSkuCheckSkipped] = useState(false);
  /** 跳过前的二次确认弹窗。 */
  const [skipConfirmOpen, setSkipConfirmOpen] = useState(false);
  const skuCheckRunRef = useRef(0);

  // 实际提交的链接数：与 startBatch 的 draft_ids 截断规则保持一致。
  const pendingLinkCount = Math.min(initialDraftIds?.length ?? 0, options.maxProducts || 100);
  // 草稿池提交时统计的 SKU 总数；缺失（如旧页签恢复）时退回已检测到的规格图数量。
  const pendingSkuCount =
    typeof initialSkuCount === 'number' && initialSkuCount > 0
      ? initialSkuCount
      : Object.values(skuResults).reduce((sum, item) => sum + (item.sku_image_count || 0), 0);

  // 失败项自动补跑（后台自动重处理）状态
  const autoRepull = batch?.auto_repull ?? null;
  const autoRepullRunning = autoRepull?.status === 'running';

  const failureItems = useMemo(
    () => batch?.items.filter(
      (item) => item.status === 'attention_required' || item.status === 'failed'
    ) || [],
    [batch]
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
  // 失败信息，进度条在全部轮次（含最多 1 轮自动重试）跑完前不显示 100%。
  // 暂停单独识别（含重试轮中的暂停），给用户明确的「已暂停」反馈。
  const processingStatuses = ['queued', 'running'];
  const batchProcessing = batch
    ? (processingStatuses.includes(batch.task.status) || autoRepullRunning)
    : false;
  const taskPaused = batch?.task.status === 'paused';
  const taskCancelled = batch?.task.status === 'cancelled';
  const taskActive = batchProcessing || taskPaused;
  const batchTotal = batch?.total_count || 0;
  const batchProcessed = batch?.processed_count ?? 0;
  // 进行中条数与最长已持续时长：从轮询返回的 items 里 status=running 的条目
  // 提取「已持续 N 秒」心跳文本，让操作员在生图/文本长耗时期间看到具体走向。
  const runningCount = batch
    ? batch.items.filter((item) => processingStatuses.includes(item.status)).length
    : 0;
  const maxOngoingSeconds = useMemo(() => {
    if (!batch) return 0;
    let max = 0;
    for (const item of batch.items) {
      if (item.status !== 'running') continue;
      // 心跳文本形如「…中 · 心跳正常 · 已持续 N 秒」
      const match = item.reason.match(/已持续\s+(\d+)\s+秒/);
      if (match) max = Math.max(max, Number(match[1]));
    }
    return max;
  }, [batch]);
  // 尾部提示：只剩 1-2 条仍在处理、且非自动重试轮时，说明正在生成较慢的主图。
  // 单张 2K 主图计费请求最长约 11 分钟（后端 660s 上限），提示操作员避免误判为卡死。
  const imageTailHint = batchProcessing
    && !autoRepullRunning
    && runningCount > 0
    && runningCount <= 2;
  // 只要任务到达终态（含整单失败/取消/完成待复核），进度都应计算为 100%，
  // 否则前端会永远被 Math.min(99,…) 卡在 99%。自动补跑轮仍在运行时除外。
  const taskDone = batch
    ? ['completed', 'partial_failure', 'failed', 'cancelled'].includes(batch.task.status) && !autoRepullRunning
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

  // SKU 检测结果统计（按本次所选链接）：clean=规格原图可用；skipped=规格图过多跳过。
  const skuTotalChecked = initialDraftIds?.length || 0;
  const skuCleanCount = useMemo(() => {
    if (!initialDraftIds?.length) return 0;
    return initialDraftIds.filter((id) => skuResults[id]?.status === 'clean').length;
  }, [initialDraftIds, skuResults]);
  const skuSkippedCount = useMemo(() => {
    if (!initialDraftIds?.length) return 0;
    return initialDraftIds.filter((id) => skuResults[id]?.status === 'skipped').length;
  }, [initialDraftIds, skuResults]);
  // 规格图还没同步完：结论暂缓，不改写取图策略，同步完成后由后端自动补判。
  const skuPendingSyncCount = useMemo(() => {
    if (!initialDraftIds?.length) return 0;
    return initialDraftIds.filter((id) => skuResults[id]?.status === 'pending_sync').length;
  }, [initialDraftIds, skuResults]);
  // 规格原图不是 1:1：店小秘「变种预览图」列强制方图，直接用原图会被整单拒收，
  // 因此同判不可用、导出回退商品主图（我们生成的方图）。
  const skuNotSquareCount = useMemo(() => {
    if (!initialDraftIds?.length) return 0;
    return initialDraftIds.filter((id) => skuResults[id]?.reason === 'not_square_image').length;
  }, [initialDraftIds, skuResults]);
  const skuFallbackCount = Math.max(0, skuTotalChecked - skuCleanCount - skuPendingSyncCount);
  // 检测进度百分比：进度条与右侧计数共用；total 为 0 时保持 0。
  const skuCheckPercent = skuCheckProgress.total > 0
    ? Math.min(100, Math.round((skuCheckProgress.done / skuCheckProgress.total) * 100))
    : 0;
  // 「已确认可复用原图」= 检测跑完且用户没有跳过；其余情况一律按生成主图处理。
  const skuCheckDecided = skuCheckDone && !skuCheckSkipped;

  // 检测结果 → 处理参数：variant_image_mode 分流 + 「优化链接 SKU」命中的变种剔除键。
  // - clean：规格图无中文，逐 SKU 用规格原图（source）；
  // - 检出中文且勾选了「优化链接 SKU」：剔除含中文的 SKU，剩余干净 SKU 仍用规格原图；
  // - 整条链接全是中文图（all_sku_chinese）：不剔除，回退商品主图，避免整条商品消失；
  // - 等待同步（pending_sync）：规格图还没物化完，此刻定论不可信，显式回落 auto（同时清掉
  //   上一轮可能写死的 main），素材同步完成后由后端自动补判，判定干净即用规格原图；
  // - 其余（无规格图 / 规格图过多 / 规格原图非 1:1 / OCR 失败）：统一用商品主图（main）。
  const skuPlan = useMemo(() => {
    const classification: Record<number, 'source' | 'main' | 'auto'> = {};
    const exclusions: Record<number, string[]> = {};
    // 没检测（用户尚未确认开始）或明确跳过：统一用生成后的主图。
    // 这是「不检测也必须可用」的兜底——规格原图是否含中文水印/是否 1:1 未知，
    // 直接复用有被店小秘整单拒收的风险。
    if (!skuCheckDecided) {
      for (const id of initialDraftIds || []) classification[id] = 'main';
      return { classification, exclusions };
    }
    for (const id of initialDraftIds || []) {
      const item = skuResults[id];
      if (!item) continue;
      if (item.status === 'pending_sync') {
        classification[id] = 'auto';
        continue;
      }
      const chineseKeys = item.chinese_variant_keys || [];
      if (
        optimizeSku
        && item.status === 'unavailable'
        && item.reason === 'chinese_detected'
        && chineseKeys.length > 0
        && !item.all_sku_chinese
      ) {
        exclusions[id] = chineseKeys;
        classification[id] = 'source';
        continue;
      }
      classification[id] = item.status === 'clean' ? 'source' : 'main';
    }
    return { classification, exclusions };
  }, [initialDraftIds, skuResults, skuCheckDecided, optimizeSku]);
  const skuOptimizedCount = Object.keys(skuPlan.exclusions).length;

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const loadTask = async (taskId: number) => {
    setTaskLoading(true);
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${taskId}/outputs?light=1`);
      setBatch(data);
    } catch (err) { fail(err); } finally { setTaskLoading(false); }
  };

  useEffect(() => {
    if (initialTaskId != null) void loadTask(initialTaskId);
    // initialTaskId only changes when WorkspaceShell opens another task tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTaskId]);

  // 进入处理设置页即自动检测所选链接的 SKU 规格图可用性：分批调用后端，逐批推进进度，
  // 全部完成后分类为 source（规格原图可用）/ main（中文水印、无规格图、规格图过多等）。
  const runSkuAvailabilityCheck = async () => {
    const draftIds = initialDraftIds || [];
    if (!draftIds.length) return;
    const runId = ++skuCheckRunRef.current;
    setSkuCheckBusy(true);
    setSkuCheckDone(false);
    setSkuCheckSkipped(false);
    setSkuCheckError('');
    setSkuResults({});
    setSkuCheckProgress({ done: 0, total: draftIds.length });
    const collected: Record<number, DraftSkuAvailabilityItem> = {};
    try {
      for (let offset = 0; offset < draftIds.length; offset += SKU_CHECK_BATCH_SIZE) {
        const chunk = draftIds.slice(offset, offset + SKU_CHECK_BATCH_SIZE);
        const data = await checkDraftSkuAvailability(ctx, chunk, false, skipSkuTextCheck);
        if (skuCheckRunRef.current !== runId) return;
        for (const item of data.results) collected[item.draft_id] = item;
        setSkuResults({ ...collected });
        setSkuCheckProgress({
          done: Math.min(offset + SKU_CHECK_BATCH_SIZE, draftIds.length),
          total: draftIds.length,
        });
      }
      setSkuCheckDone(true);
    } catch (err) {
      if (skuCheckRunRef.current !== runId) return;
      setSkuCheckError(err instanceof Error ? err.message : String(err));
    } finally {
      if (skuCheckRunRef.current === runId) setSkuCheckBusy(false);
    }
  };

  // 不再自动检测：这一步要读每张规格图跑 OCR，在低配电脑上会明显拖慢进页面；
  // 改为用户点「开始检测」才跑。不检测/跳过时 SKU 图统一用生成后的主图（见 skuPlan）。
  useEffect(() => () => { skuCheckRunRef.current += 1; }, []);

  const downloadOutput = async (kind: 'dxm' | 'errors' | 'video_manifest', filename: string) => {
    if (!batch) return;
    try {
      await ppDownload(ctx, `${API_BASE}/tasks/${batch.task_id}/download?kind=${kind}`, filename);
    } catch (err) { fail(err); }
  };

  // 关闭页面时告诉后端「这个任务别再跑了」：pagehide 里用 sendBeacon 发离开信标。
  // 之后只要还有页面在轮询（刷新/重开工作台/另一个页签还在看，包括被浏览器节流到、
  // 1 分钟一次的隐藏页），这次离开就会被撤销；只有连续静默才会真的暂停。
  // 只认 pagehide——页面被节流/冻结、电脑休眠都不会触发它，那些情况任务照跑，
  // 不再按「多久没心跳」猜（猜测式判定正是「工作台开着却被暂停」的来源）。
  useEffect(() => {
    const status = batch?.task.status;
    if (!batch || (status !== 'queued' && status !== 'running')) return;
    const taskId = batch.task_id;
    const workspace = ctx.workspaceId;
    const leave = () => {
      navigator.sendBeacon?.(`${API_BASE}/tasks/${taskId}/leave?workspace=${encodeURIComponent(workspace)}`);
    };
    window.addEventListener('pagehide', leave);
    return () => window.removeEventListener('pagehide', leave);
  }, [batch?.task.status, batch?.task_id, ctx.workspaceId]);

  useEffect(() => {
    if (!batch) return;
    // 非激活页签（工作台里被 hidden 的页签）只做低频「租约」轮询：后端靠 /outputs 的
    // 访问时刻撤销离开信标，完全停掉会让运行中任务在 30 分钟后被判定为「没人看」而暂停。
    // 因此改为每 30 秒打一次轻量 /summary（最多 20 条、不读取大字段），切回页签时本
    // effect 因 isActive 变化重跑，立刻恢复 1 秒全量轮询。
    const leaseOnly = !isActive;
    const activeStatuses = ['queued', 'running', 'paused'];
    const isTaskActive = (status: string, repullStatus?: string) =>
      activeStatuses.includes(status) || repullStatus === 'running';
    const generation = ++pollGenerationRef.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    // 失败退避：后端被图像计算占满时，1 秒一次的必然失败请求只会加剧拥塞。
    let retryDelay = 1000;
    // 内容指纹：数据没变就不 setBatch，避免每秒整页重渲染（进度环/失败表/统计）。
    let lastPayload = '';
    // 只在"确实盯过运行中任务"之后才做终态追跟，避免打开历史终态任务时空转轮询。
    let sawActive = isTaskActive(batch.task.status, batch.auto_repull?.status);
    let terminalSince: number | null = null;
    // 运行中登记（供应用壳续心跳 / 失联恢复时避免整页刷新把本页清掉）。
    let unregisterRunning: (() => void) | null = null;
    const syncWatch = (stillActive: boolean) => {
      if (stillActive && !unregisterRunning) {
        unregisterRunning = registerRunningTask(batch.task_id);
      } else if (!stillActive && unregisterRunning) {
        unregisterRunning();
        unregisterRunning = null;
      }
    };
    const poll = async () => {
      // 隐藏页签只发轻量租约请求，绝不 setBatch（/summary 会截断 items）。
      if (leaseOnly) {
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
          timer = setTimeout(poll, 60_000);
          return;
        }
        try {
          await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/summary`);
        } catch {
          // 租约失败不提示用户：切回页签时会立刻全量刷新。
        }
        if (!stopped) timer = setTimeout(poll, 30_000);
        return;
      }
      // 页签隐藏时降频，避免后台页签每秒拉一次全量结果拖慢前台操作。
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        timer = setTimeout(poll, 4000);
        return;
      }
      try {
        const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/outputs?light=1`);
        if (stopped || pollGenerationRef.current !== generation) return;
        retryDelay = 1000;
        const payload = JSON.stringify(data);
        if (payload !== lastPayload) {
          lastPayload = payload;
          setBatch(data);
        }
        const stillActive = isTaskActive(data.task.status, data.auto_repull?.status);
        syncWatch(stillActive);
        if (stillActive) {
          sawActive = true;
          terminalSince = null;
        } else {
          if (!sawActive) return;
          terminalSince = terminalSince ?? Date.now();
          if (Date.now() - terminalSince >= TERMINAL_POLL_GRACE_MS) return;
          timer = setTimeout(poll, TERMINAL_POLL_INTERVAL_MS);
          return;
        }
      } catch (err) {
        if (stopped || pollGenerationRef.current !== generation) return;
        fail(err);
        timer = setTimeout(poll, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 10_000);
        return;
      }
      timer = setTimeout(poll, 1000);
    };
    syncWatch(sawActive);
    timer = setTimeout(poll, 1000);
    return () => {
      stopped = true;
      pollGenerationRef.current += 1;
      if (timer) clearTimeout(timer);
      if (unregisterRunning) unregisterRunning();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.task_id, batch?.task.status, batch?.auto_repull?.status, isActive]);

  // 尺寸卡/原图复用已改到后端按条并发执行：每条结果落库后立即烧第 4 格，
  // 与其余链接的 AI 调用重叠，不再等整批结束后由前端补跑。

  const startBatch = async () => {
    if (!initialDraftIds?.length) { setError('没有可处理的草稿'); return; }
    if (startInFlightRef.current || batchProcessing) return;
    startInFlightRef.current = true;
    setLoading(true);
    setMessage('');
    setError('');
    // 真正发起处理时把当前组合记入「历史预设」，下次可直接一键回填。
    setComboHistory(
      pushComboHistory(
        buildComboSnapshot({
          mode: options.imageMode || 'simple',
          presetId: options.imagePreset || 'auto',
          category: options.imageCategoryOverride || 'auto',
          layers: imageSelection,
          panels: options.imagePanels || DEFAULT_PANEL_SELECTIONS,
        }),
      ),
    );
    try {
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
        desc_template: options.descriptionTemplate || 'A',
        // 批次级品类组合：auto=每个链接按标题自动分类；指定品类=整批统一用该组合。
        image_category_override: options.imageCategoryOverride || 'auto',
        // 五层卡片：auto=品类默认；指定卡 id=整批统一。
        image_frame: options.imageFrame || 'auto',
        image_light: options.imageLight || 'auto',
        image_tone: options.imageTone || 'auto',
        image_detail: options.imageDetail || 'auto',
        image_style: options.imageStyle || 'auto',
        // 四格逐格卡片：每一格单独指定场景、道具和五层。
        image_panels: (options.imagePanels || DEFAULT_PANEL_SELECTIONS).map((panel) => ({
          ...panel,
          white_hero: panel.whiteHero,
        })),
        // 导演模式：逐格卡片是用户显式设置的，后端会按高优先级逐格改写下发，
        // 不再被模板里写死的机位/道具数量盖掉。
        image_panel_override: options.imageMode === 'pro',
        // 小白模板商业意图：四套模板除了换卡片，还会附加“买家一眼要看什么”。
        image_preset: options.imagePreset || 'auto',
        // 「随机模板」：本批次每条链接随机分配一个效果模板；模板与四格卡片成对切换，
        // 因此把四个模板的逐格卡片一起下发，由后端按 (task, draft) 稳定哈希分配。
        random_image_preset: options.randomImagePreset === true,
        image_preset_pool: options.randomImagePreset
          ? SIMPLE_PRESETS.map((preset) => preset.id)
          : [],
        image_preset_panels: options.randomImagePreset
          ? Object.fromEntries(
              SIMPLE_PRESETS.map((preset) => [
                preset.id,
                preset.panels.map((panel) => ({ ...panel, white_hero: panel.whiteHero })),
              ]),
            )
          : {},
        // 一键随机模板：默认关闭，打开后本批次每条链接随机分配生图/描述模板。
        random_template: options.randomTemplate === true,
        // 用户选择是否对技术可重试的失败项自动补跑（默认关闭，需显式勾选）
        auto_repull: options.autoRepull === true,
        // 是否自动烧制第 4 格尺寸图（默认关闭，需用户在处理设置里显式勾选）
        dimension_card: options.dimensionCard === true,
        // 兼容旧 API 字段；新任务统一走智能生图策略，不再由用户选择。
        image_generation_count: 4,
        // SKU 原图可用性分类（处理设置页检测结果）：写入草稿预检覆盖，导出按此分流。
        variant_image_classification: skuPlan.classification,
        // 「优化链接 SKU」命中的来源变种：导出时整行剔除。
        variant_image_exclusions: skuPlan.exclusions,
      };
      const signature = JSON.stringify(body);
      if (!startRequestRef.current || startRequestRef.current.signature !== signature) {
        startRequestRef.current = {
          signature,
          key: globalThis.crypto?.randomUUID?.() || `pp-${Date.now()}-${Math.random()}`,
        };
      }
      const premiumIds = (initialPremiumDraftIds || []).filter((id) => initialDraftIds.includes(id));
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/drafts/process`, {
        body: { ...body, premium_draft_ids: premiumIds },
        headers: { 'Idempotency-Key': startRequestRef.current.key },
      });
      setBatch(data);
      onTaskStarted?.(data.task_id);
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

  // 暂停后只保留已成功商品进入预检；确认后剩余商品永久取消，任务不可恢复。
  const finalizeSuccessfulItems = async () => {
    if (!batch || !taskPaused || controlBusy || batch.success_count <= 0) return;
    const remaining = batch.items.filter(
      (item) => item.status === 'pending' || item.status === 'running'
    ).length;
    if (!window.confirm(
      `将保留当前 ${batch.success_count} 个成功商品进入预检，并永久取消剩余 ${remaining} 个未完成商品。取消后不能继续处理，确认导出？`
    )) return;
    setControlBusy(true);
    try {
      const data = await ppRequest<TaskOutputsResponse>(
        ctx,
        `${API_BASE}/tasks/${batch.task_id}/finalize-successes`,
        { body: {} },
      );
      setBatch(data);
      notify(data.message || `已保留 ${data.success_count} 个成功商品进入预检`);
      onOpenPrecheck?.(batch.task_id);
    } catch (err) { fail(err); }
    finally { setControlBusy(false); }
  };

  // 取消：终态操作，立即停止后续 AI 调用，未处理链接标记失败并释放（不再产生 AI 费用）。
  const cancelTask = async () => {
    if (!batch || controlBusy) return;
    if (!window.confirm('确定取消该任务？未处理的链接将标记为失败并立即释放积分，任务不可恢复（可重新提交草稿处理）。')) return;
    setControlBusy(true);
    try {
      const data = await ppRequest<TaskOutputsResponse>(ctx, `${API_BASE}/tasks/${batch.task_id}/cancel`, { body: {} });
      setBatch(data);
      notify((data as { message?: string }).message || '任务已取消，未处理链接已释放');
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
        body: { draft_ids: draftIds, auto_repull: options.autoRepull === true },
      });
      setBatch(data);
      onTaskStarted?.(data.task_id);
      notify(data.message || `已提交 ${draftIds.length} 个失败项重新处理`);
    } catch (err) { fail(err); } finally { startInFlightRef.current = false; setLoading(false); }
  };

  return (
    <div className="verify-page verify-task-page">
      {batchProcessing && createPortal(
        <div className="daily-topbar-status is-progress" role="status" aria-label="处理进度">
          <span className="daily-topbar-status-icon" aria-hidden="true">↻</span>
          <strong>正在处理</strong>
          <div className="topbar-collection-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <b>{progress}%</b>
        </div>,
        // portal 到工作台顶栏的专用插槽：面板 fill-mode 动画的层叠上下文
        // 会把 fixed 子元素锁在面板内、被 sticky 顶栏盖住（与每日选品页同款修法）。
        document.getElementById("workspace-topbar-status") || document.body,
      )}
      {startConfirmOpen && createPortal(
        <div
          className="pp-start-confirm-mask"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setStartConfirmOpen(false);
          }}
        >
          <div
            className="pp-start-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pp-start-confirm-title"
          >
            <h3 id="pp-start-confirm-title">确认开始处理</h3>
            <p className="pp-start-confirm-line">
              本批次共计 <b>{pendingLinkCount}</b> 条链接，共 <b>{pendingSkuCount}</b> 个 SKU，是否继续处理？
            </p>
            <p className="pp-start-confirm-hint">SKU 过多会导致后续低配置电脑处理卡顿。</p>
            <div className="pp-start-confirm-actions">
              <button
                className="primary"
                onClick={() => {
                  setStartConfirmOpen(false);
                  void startBatch();
                }}
              >是</button>
              <button
                onClick={() => {
                  setStartConfirmOpen(false);
                  onBackToDraftPool?.();
                }}
              >返回草稿池删减 SKU</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
      {skipConfirmOpen && createPortal(
        <div
          className="pp-start-confirm-mask"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSkipConfirmOpen(false);
          }}
        >
          <div
            className="pp-start-confirm pp-sku-skip-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pp-sku-skip-title"
          >
            <h3 id="pp-sku-skip-title">跳过 SKU 原图检测？</h3>
            <p className="pp-start-confirm-line">
              跳过检测后，本批 <b>{pendingLinkCount}</b> 条链接的 SKU 图将统一使用
              <b>AI 生成后的商品主图</b>，不再复用规格原图。
            </p>
            <ul className="pp-sku-skip-points">
              <li>好处：不必等待逐张 OCR，开始处理更快；主图天然是 1:1 方图，不会被店小秘拒收。</li>
              <li>代价：规格原图不再被复用，SKU 图会与原始规格图不同（但仍与本批主图风格一致）。</li>
              <li>随时可点「重新检测」恢复原图复用，检测结果会自动更新导出策略。</li>
            </ul>
            <div className="pp-start-confirm-actions">
              <button className="primary" onClick={() => setSkipConfirmOpen(false)}>返回检测</button>
              <button
                onClick={() => {
                  setSkipConfirmOpen(false);
                  setSkuCheckSkipped(true);
                  setSkuCheckError('');
                }}
              >仍然跳过，用生成主图</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>{initialTaskId != null ? '历史任务详情' : '处理设置与进度'}</h1>
          <p>{initialTaskId != null ? '继续处理中的历史任务；关闭本页后超过半小时没人回来看才会暂停（可从左侧“历史记录”继续），切到别的界面或最小化窗口都不影响运行。' : '配置处理参数后开始，结果实时轮询。暂停/取消都会立即停止后续 AI 调用。'}</p>
        </div>
      </header>

      {message && (
        <div className="verify-toast" role="status">{message}</div>
      )}

      {error && (
        <div className="verify-message error">{error}</div>
      )}

      <div className="task-layout">
        {/* 左侧：处理设置 + 处理结果 */}
        <div className="task-main">

          {initialTaskId == null && <section className="verify-section verify-settings-section">
            <div className="verify-section-head">
              <h2>处理设置</h2>
              <span className="verify-sub">站点 / 语言 / 生图组合</span>
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
              <div className="verify-inline-checks">
                <label className="verify-scope-check verify-inline-check" title="任务结束后自动重试失败/待确认的链接（每条最多自动重试 1 次，避免无限重试）。重试会再次调用 AI 生图、产生 AI 生图费用；默认关闭，需要时勾选，关闭后失败项可在结果页手动重新处理">
                  <input
                    type="checkbox"
                    checked={options.autoRepull === true}
                    onChange={(e) => setOptions((p) => ({ ...p, autoRepull: e.target.checked }))}
                  />
                  <span>自动重跑失败项</span>
                  <span className="verify-sub">最多 1 次 · 会再次调用 AI 并计费</span>
                </label>
                <label className="verify-scope-check verify-inline-check" title="勾选后，AI 处理成功时自动把第 4 格烧成尺寸卡（复用原链接尺寸图或本地渲染）。默认关闭；开启后不导入尺寸画布之外的额外处理，不需要尺寸图的品类保持关闭即可">
                  <input
                    type="checkbox"
                    checked={options.dimensionCard === true}
                    onChange={(e) => setOptions((p) => ({ ...p, dimensionCard: e.target.checked }))}
                  />
                  <span>自动烧制尺寸图</span>
                  <span className="verify-sub">第 4 格尺寸卡 · 默认关闭，按品类开启</span>
                </label>
              </div>
            </div>
            <ImageComboPicker
              mode={options.imageMode || 'simple'}
              onModeChange={(mode) => setOptions((p) => ({ ...p, imageMode: mode }))}
              category={options.imageCategoryOverride || 'auto'}
              onCategoryChange={(id: ImageCategoryId) =>
                setOptions((p) => ({ ...p, imageCategoryOverride: id }))
              }
              selection={imageSelection}
              onSelectionChange={setImageLayer}
              onApplyPreset={applyImageSelection}
              panels={options.imagePanels || DEFAULT_PANEL_SELECTIONS}
              onPanelsChange={(panels: PanelSelection[]) =>
                setOptions((p) => ({ ...p, imagePanels: panels, imagePreset: 'auto' }))
              }
              randomPreset={options.randomImagePreset === true}
              onRandomPresetChange={(random) =>
                setOptions((p) => ({ ...p, randomImagePreset: random }))
              }
              history={comboHistory}
              onApplyHistory={applyComboSnapshot}
            />
            <div className="verify-sku-check">
              <div className="verify-sku-check-head">
                <span className="verify-sku-check-title">SKU 原图可用性检测</span>
                <span className="verify-sub">
                  {skuCheckBusy
                    ? `检测中 ${skuCheckProgress.done}/${skuCheckProgress.total}`
                    : skuCheckDone
                      ? '检测完成'
                      : skuCheckSkipped
                        ? '已跳过 · 用生成主图'
                        : skuCheckError
                          ? '检测失败'
                          : '等待确认'}
                </span>
                <label
                  className="verify-scope-check"
                  title="勾选后：检测完成时自动删除含中文规格图的链接中对应的 SKU（其余干净 SKU 仍用规格原图导出）；整条链接全是中文图时不删除，改为回退商品主图"
                >
                  <input
                    type="checkbox"
                    checked={optimizeSku}
                    onChange={(e) => setOptimizeSku(e.target.checked)}
                  />
                  <span>优化链接 SKU</span>
                </label>
                <label
                  className="verify-scope-check"
                  title="勾选后：只判规格图是否为 1:1 方图（读元数据，零成本），跳过逐张中文 OCR。非 1:1 仍会被拦住；代价是规格原图上的中文水印不会被发现，导出风险自负。"
                >
                  <input
                    type="checkbox"
                    checked={skipSkuTextCheck}
                    onChange={(e) => setSkipSkuTextCheck(e.target.checked)}
                  />
                  <span>跳过中文检测（只判 1:1，快）</span>
                </label>
                {!skuCheckBusy && !skuCheckDone && !skuCheckSkipped && (
                  <>
                    <button
                      className="primary verify-sku-start"
                      disabled={!initialDraftIds?.length}
                      onClick={() => void runSkuAvailabilityCheck()}
                      title={skipSkuTextCheck
                        ? '只判规格图是否为 1:1 方图（快）'
                        : '逐张检测规格图是否含中文水印、是否为 1:1 方图'}
                    >开始检测</button>
                    <button
                      className="btn-mini"
                      disabled={!initialDraftIds?.length}
                      onClick={() => setSkipConfirmOpen(true)}
                      title="不检测：本批 SKU 图统一使用生成后的主图"
                    >跳过，用生成主图</button>
                  </>
                )}
                {!skuCheckBusy && (skuCheckDone || skuCheckSkipped || !!skuCheckError) && (
                  <button
                    className="btn-mini"
                    disabled={!initialDraftIds?.length}
                    onClick={() => void runSkuAvailabilityCheck()}
                    title={skipSkuTextCheck
                      ? '重新检测：只判规格图是否为 1:1 方图，跳过中文 OCR（快）'
                      : '重新检测所选链接的 SKU 规格图是否含中文水印、以及是否为 1:1 方图'}
                  >重新检测</button>
                )}
                {/* 开始处理/清空任务跟检测操作放在同一行：检测完即可直接开跑。 */}
                <div className="verify-actions verify-sku-actions">
                  <button className="primary" onClick={() => setStartConfirmOpen(true)} disabled={loading || batchProcessing || !initialDraftIds?.length || skuCheckBusy}>{loading ? <><i className="app-spinner is-sm" aria-hidden="true" />处理中...</> : '开始处理'}</button>
                  <button onClick={clearBatch} disabled={!batch || batchProcessing} title={batchProcessing ? '运行中任务不能清理' : undefined}>清空任务</button>
                  {!!initialPremiumDraftIds?.length && (
                    <span className="verify-premium-hint">精品模式 {initialPremiumDraftIds.length} 条</span>
                  )}
                </div>
              </div>
              {/* 用途说明：这一步决定 SKU 图能否直接复用原图链接（与主图保持同一套视觉）。 */}
              <p className="verify-sku-check-purpose">
                这一步决定本批 <b>SKU 图能否直接复用原图链接</b>：逐张检查规格图是否含中文水印、是否为 1:1 方图。
                可复用时沿用规格原图（与主图同一套视觉、导出更一致）；不可用、未检测或跳过时，SKU 图统一改用<b>生成后的主图</b>。
              </p>
              {skuCheckBusy && (
                <div
                  className="verify-sku-progress"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={skuCheckPercent}
                  aria-label="SKU 原图可用性检测进度"
                >
                  <div className="verify-sku-progress-track is-running">
                    <div className="verify-sku-progress-bar" style={{ width: `${skuCheckPercent}%` }} />
                  </div>
                  <div className="verify-sku-progress-meta">
                    <span>
                      {skipSkuTextCheck
                        ? '正在检测所选链接的 SKU 规格图是否为 1:1 方图（已跳过中文 OCR）…'
                        : '正在检测所选链接的 SKU 规格图是否含中文水印、是否为 1:1 方图…'}
                    </span>
                    <em>{skuCheckProgress.done} / {skuCheckProgress.total} 条 · {skuCheckPercent}%</em>
                  </div>
                </div>
              )}
              {!skuCheckBusy && skuCheckSkipped && (
                <div className="verify-repull-banner is-skipped" role="status">
                  <i className="iconfont icon-infomation" aria-hidden="true" />
                  <span>
                    已跳过检测：本批 SKU 图将统一使用<b>生成后的主图</b>，不再复用规格原图。
                    需要复用原图时点「重新检测」即可。
                  </span>
                </div>
              )}
              {!skuCheckBusy && !skuCheckSkipped && !skuCheckDone && !skuCheckError && (
                <div className="verify-repull-banner is-idle" role="status">
                  <i className="iconfont icon-infomation" aria-hidden="true" />
                  <span>尚未检测：直接开始处理将默认用<b>生成后的主图</b>代替 SKU 图。</span>
                </div>
              )}
              {!skuCheckBusy && !!skuCheckError && (
                <div className="verify-repull-banner failed" role="status">
                  <i className="iconfont icon-infomation" aria-hidden="true" />
                  <span>检测失败：{skuCheckError}；可点「重新检测」。直接开始处理将按默认策略用生成后的主图代替 SKU 图。</span>
                </div>
              )}
              {!skuCheckBusy && !skuCheckError && skuCheckDone && (
                <>
                  <div className="verify-summary">
                    <div className="verify-count success">可用原图 <b>{skuCleanCount}</b></div>
                    <div className="verify-count">改用主图 <b>{skuFallbackCount}</b></div>
                    <div className="verify-count">规格图过多跳过 <b>{skuSkippedCount}</b></div>
                    {skuNotSquareCount > 0 && (
                      <div className="verify-count">原图非 1:1 <b>{skuNotSquareCount}</b></div>
                    )}
                    {skuPendingSyncCount > 0 && (
                      <div className="verify-count">等待同步 <b>{skuPendingSyncCount}</b></div>
                    )}
                  </div>
                  <p className="verify-sku-check-hint">
                    共检测 <b>{skuTotalChecked}</b> 条链接：<b>{skuCleanCount}</b> 条 SKU 规格图可用，导出时用「规格原图」；
                    <b>{skuFallbackCount}</b> 条改用「商品主图」（含 {skuSkippedCount} 条 SKU 规格图 ≥ 50 张的链接
                    {skuNotSquareCount > 0 && <>，以及 {skuNotSquareCount} 条规格原图不是 1:1 的链接</>}）。
                    {skuNotSquareCount > 0 && (
                      <>
                        {' '}店小秘要求「变种预览图必须为 1:1 尺寸」，这些链接直接导规格原图会被拒收，
                        已自动改用 1:1 的商品主图；如需保留原图，请先在预检页把图片裁成正方形。
                      </>
                    )}
                    {skuPendingSyncCount > 0 && (
                      <>
                        {' '}另有 <b>{skuPendingSyncCount}</b> 条规格图还在同步，暂不定论——同步完成后会自动补判，
                        判定干净即改用「规格原图」，无需手动重检。
                      </>
                    )}
                    分类已自动预置，可在预检页逐条修改。
                  </p>
                </>
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
                  <div className={`verify-progress-area ${batchProcessing ? 'is-live' : 'is-done'}`}>
                    <ProgressRing progress={progress} running={batchProcessing} />
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
                      {batchProcessing && runningCount > 0 && (
                        <span className="verify-live-detail">
                          进行中 <b>{runningCount}</b> 条
                          {maxOngoingSeconds > 0 && <> · 最长已持续 {formatDuration(maxOngoingSeconds)}</>}
                          {imageTailHint && (
                            <span className="verify-tail-hint">
                              {' '}· 正在生成最后的主图，单张最长约 11 分钟，请耐心等待
                            </span>
                          )}
                        </span>
                      )}
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
                  {/* 未处理：任务已到终态但仍有没跑完的条目（正常终态应为 0，恒不显示）。
                      历史收尾竞态曾把这类任务误判「完成」，未处理项在汇总里完全隐形；
                      保留该格子让剩余条目可见，用户可循「失败项重新处理」或重提草稿处置。 */}
                  {(batch.not_processed_count ?? 0) > 0 && (
                    <div className="verify-count">未处理 <b>{batch.not_processed_count}</b></div>
                  )}
                  <div className="verify-count">待确认 <b>{batch.attention_required_count}</b></div>
                  <div className="verify-count">技术可重试 <b>{batch.technical_retryable_count}</b></div>
                  <div className="verify-count">配置阻断 <b>{batch.configuration_blocked_count}</b></div>
                </div>
                )}
                {taskCancelled && (
                  <div className="verify-repull-banner paused" role="status">
                    <i className="iconfont icon-infomation" aria-hidden="true" />
                    <span>任务已取消：未处理的链接已标记失败并释放积分，不再产生 AI 费用；可重新提交草稿处理。</span>
                  </div>
                )}
                <div className="verify-actions">
                  {(batchProcessing || taskPaused) && (
                    <button
                      className={batch.task.status === 'paused' ? 'primary' : ''}
                      disabled={controlBusy || loading}
                      onClick={() => void (batch.task.status === 'paused' ? resumeTask() : pauseTask())}
                      title={batch.task.status === 'paused' ? '继续处理剩余商品' : '暂停处理：停止后续 AI 调用，可随时从断点继续'}
                    >{controlBusy && <i className="app-spinner is-sm" aria-hidden="true" />}{batch.task.status === 'paused' ? '继续处理' : '暂停处理'}</button>
                  )}
                  {(batchProcessing || taskPaused) && (
                    <button
                      className="danger"
                      disabled={controlBusy || loading}
                      onClick={() => void cancelTask()}
                      title="取消任务：立即停止后续 AI 调用，未处理链接标记失败并释放积分（不可恢复）"
                    >{controlBusy && <i className="app-spinner is-sm" aria-hidden="true" />}取消任务</button>
                  )}
                  {taskPaused ? (
                    <button
                      className="primary"
                      disabled={controlBusy || loading || batch.success_count <= 0}
                      onClick={() => void finalizeSuccessfulItems()}
                      title={batch.success_count > 0
                        ? '永久取消剩余未完成商品，只预检并导出当前成功商品'
                        : '当前还没有成功商品可导出'}
                    >{controlBusy && <i className="app-spinner is-sm" aria-hidden="true" />}预检并导出成功商品（{batch.success_count}）</button>
                  ) : (
                    <button
                      className="primary"
                      disabled={taskActive || batch.success_count <= 0}
                      onClick={() => onOpenPrecheck?.(batch.task_id)}
                      title={taskActive
                        ? '处理完成后可进入预检'
                        : batch.success_count <= 0
                          ? '当前没有成功商品可预检导出'
                          : '打开预检页：核对标题/图片/字段，修改后导出最终版表格'}
                    >{taskCancelled ? '预检并导出成功商品' : '预检并导出最终版'}</button>
                  )}
                  {batch.outputs.product_video_manifest && <button onClick={() => void downloadOutput('video_manifest', `product_video_manifest_task_${batch.task_id}.csv`)}>下载视频清单</button>}
                </div>
              </>
            )}
            {!batch && taskLoading && (
              <div className="app-loading-block" role="status" aria-live="polite">
                <i className="app-spinner" aria-hidden="true" />
                <span>正在读取任务结果…</span>
              </div>
            )}
            {!batch && !taskLoading && <p className="verify-empty">尚未提交处理批次。请配置参数后点击"开始处理"。</p>}
          </section>

          {/* 失败明细只在全部处理（含最多 1 轮自动重试）跑完后展示最终失败链接及原因 */}
          {failureItems.length > 0 && !taskActive && (
            <section className="verify-section">
              <div className="verify-section-head">
                <h2>失败商品 <span className="verify-failure-count">（{failureItems.length} 项）</span></h2>
                <span className="verify-actions">
                  <button className="btn-mini" onClick={() => setShowFailures((v) => !v)} title={showFailures ? '收起失败明细' : '展开失败明细，可手动重新处理'}>{showFailures ? '收起明细' : '查看明细'}</button>
                  {showFailures && (
                    <>
                  {failureItems.length > 0 && (
                    <button
                      className="btn-mini primary"
                      disabled={loading || batchProcessing}
                      onClick={() => retryFailed(failureItems.map((item) => item.product_draft_id).filter((id): id is number => id != null))}
                      title={batchProcessing ? '当前批次处理中，完成后可重试' : '重新提交全部失败链接处理'}
                    >{loading ? <><i className="app-spinner is-sm" aria-hidden="true" />重试全部失败（{failureItems.length}）</> : <><i className="iconfont icon-rocket" aria-hidden="true" />重试全部失败（{failureItems.length}）</>}</button>
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
                        <td>失败</td>
                        <td>{FAILURE_CLASS_LABELS[failureClass] || '已过滤'}</td>
                        <td>
                          {item.reason || FAILURE_FILTER_HINT}
                          {result.debug_hint ? (
                            // 客户只看可读原因；原始技术原因（生图回退/底层异常等）折叠在
                            // 「技术详情」里，供运营排障，不干扰客户阅读。
                            <details className="verify-failure-detail">
                              <summary>技术详情</summary>
                              <span>{String(result.debug_hint)}</span>
                            </details>
                          ) : null}
                        </td>
                        <td>{String(result.operator_hint || '') || FAILURE_FILTER_HINT}</td>
                        <td>{result.retryable ? '是' : '否'}</td>
                        <td>
                          {draftId != null ? (
                            <span className="verify-row-actions">
                              <button
                                className="btn-mini primary"
                                disabled={loading || batchProcessing}
                                onClick={() => retryFailed([draftId])}
                                title="以该草稿重新提交处理流水线"
                              >{loading && <i className="app-spinner is-sm" aria-hidden="true" />}重新处理</button>
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
