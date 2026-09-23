import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { getAuthAccount, getAuthToken } from '../../../transport/http/client';
import type { ApiContext } from '../../product_processing/api/client';
import {
  autoMaskItem,
  comboKitGeneratedUrl,
  comboKitOriginUrl,
  createPreview,
  createSet,
  deleteGeneratedImage,
  exportComboDianxiaomi,
  getComboKitTask,
  getRoles,
  applyWatermark,
  getSet,
  listSets,
  reorderItems,
  removeItem,
  reviewPreview,
  savePrompt,
  setPrimaryItem,
  startAnalyzeSubject,
  startGenerateImages,
  startGenerateText,
  updateItem,
  updateSet,
  uploadItem,
  type ComboGenerationMode,
  type ComboImageRole,
  type ComboKitItem,
  type ComboKitSet,
  type ComboKitTask,
  type ComboKitTaskType,
  type ComboKitWatermark,
  type ComboKitWatermarkPosition,
  type ComboRoles,
} from '../../product_processing/api/comboKitApi';
import { MaskCanvas } from '../components/MaskCanvas';
import { ModeCardCarousel, type ModeCardSlide } from '../components/ModeCardCarousel';
import { ProductFlowSteps, type ProductFlowStep } from '../../product_processing/components/ProductFlowSteps';
import { COMBO_PRESET_TEMPLATES, resolveActiveTemplate } from '../presetTemplates';
import '../styles/comboKit.css';

type Props = { isActive?: boolean; initialSetId?: string };

// 组合套装六步工作流：沿用「产品处理工作流」的步骤卡视觉，文案按套装实际流程改写。
// 单品多视角选型不做融合，第③步文案随之切换。
function buildFlowSteps(isMultiview: boolean): ProductFlowStep[] {
  return [
    { id: '1', number: '01', title: '套装信息', description: '填写名称、SKU、生成选型与店小秘必填字段' },
    { id: '2', number: '02', title: '上传原图', description: '上传 2~6 张原图，可粘贴截图；填视角/主体词并框选' },
    {
      id: '3',
      number: '03',
      title: isMultiview ? '商品主图' : '融合主图',
      description: isMultiview ? '解析各视角信息，直接生成单品商品主图' : '解析各商品主体，生成融合套装主图',
    },
    { id: '4', number: '04', title: 'AI 文本', description: '生成标题、描述与五点卖点' },
    { id: '5', number: '05', title: '成品图', description: '并行生成 6 张成品图，可单张替换' },
    { id: '6', number: '06', title: '预检', description: '独立预检、过图床并导出店小秘' },
  ];
}

// 套装各步之间无强制前置校验，随时可切换查看（与改造前的步骤标签行为一致）。
const COMBO_FLOW_ALWAYS_OPEN = () => true;

// 三个长耗时 AI 动作在后端异步执行：提交后按固定间隔轮询任务状态。
const TASK_POLL_INTERVAL_MS = 1500;
// 轮询兜底上限：远超实际耗时，仅用于避免服务端异常时无限轮询。
const TASK_POLL_TIMEOUT_MS = 30 * 60 * 1000;

// 可单独「替换」重做的生图角色（对应后端单张重做接口）；主图走③融合、详情为本地拼接，不在此列。
const REGENERATABLE_ROLES = ['carousel_2', 'carousel_3', 'white_bg', 'detail_shot'];

function api(): ApiContext {
  const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
  return {
    baseUrl: '',
    token: getAuthToken(),
    workspaceId: account.workspace_id || account.workspace_code || 'default',
  };
}

// 预置套装类目：组合套装作为「多件套/礼包」售卖，类目应体现套装性质，
// 而非某一成员商品（如耳机包）的单一类目。选择即写入 category_name。
const PRESET_SET_CATEGORIES = [
  '礼品套装',
  '办公文具套装',
  '数码配件套装',
  '家居生活套装',
  '美妆个护套装',
  '厨房用品套装',
  '户外运动套装',
  '玩具游戏套装',
  '汽车用品套装',
  '宠物用品套装',
];

const EMPTY_FORM = {
  name: '',
  sku: '',
  sku_display: '',
  description: '',
  category_path: '',
  category_id: '',
  spec: '',
  declared_price: '',
  length_cm: '',
  width_cm: '',
  height_cm: '',
  weight_g: '',
  stock: '',
  category_name: '',
  suggested_price_usd: '',
  id_type: '',
  id_code: '',
  // 生成选型（bundle=套装组合 / multiview=单品多视角），默认与后端默认一致。
  generation_mode: 'bundle',
};

// 选型的兜底清单：/roles 未返回时仍有卡片可渲染（正常情况下以后端下发为准）。
const FALLBACK_GENERATION_MODES = [
  { mode: 'bundle', label: '套装组合', description: '2~6 件不同商品组成一个套装：先把成员商品融合成一张套装主图，再派生成品图。' },
  { mode: 'multiview', label: '单品多视角', description: '同一商品的多张视角图（正面/侧面/内部/包装展开图）：不融合，直接以全部视角图为参考生成商品主图。' },
];

// 成品图可选文字水印（每个套装各自配置，默认关闭）。
const EMPTY_WATERMARK: ComboKitWatermark = {
  enabled: false,
  text: '',
  position: 'bottom_right',
  opacity: 30,
  size: 5,
  tile: false,
};

const WATERMARK_POSITIONS: Array<{ value: ComboKitWatermarkPosition; label: string }> = [
  { value: 'bottom_right', label: '右下角' },
  { value: 'bottom_left', label: '左下角' },
  { value: 'top_right', label: '右上角' },
  { value: 'top_left', label: '左上角' },
  { value: 'center', label: '居中' },
];

const readWatermark = (data: ComboKitSet): ComboKitWatermark => {
  const raw = (data.watermark_json || {}) as Partial<ComboKitWatermark>;
  const position = WATERMARK_POSITIONS.some((p) => p.value === raw.position)
    ? (raw.position as ComboKitWatermarkPosition)
    : EMPTY_WATERMARK.position;
  return {
    enabled: !!raw.enabled,
    text: String(raw.text ?? ''),
    position,
    opacity: typeof raw.opacity === 'number' ? raw.opacity : EMPTY_WATERMARK.opacity,
    size: typeof raw.size === 'number' ? raw.size : EMPTY_WATERMARK.size,
    tile: !!raw.tile,
  };
};

export function ComboKitPage({ isActive = true, initialSetId }: Props) {
  const ctx = useMemo(() => api(), []);
  const [roles, setRoles] = useState<ComboRoles | null>(null);
  const [set, setSet] = useState<ComboKitSet | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  // 异步任务的当前进度文案（如「解析主体 2/5」），进行中按钮据此显示。
  const [progressText, setProgressText] = useState('');
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [baseA, setBaseA] = useState('');
  const [fusionPrompt, setFusionPrompt] = useState('');
  const [textResult, setTextResult] = useState<Record<string, unknown> | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  // 新建面板的生成选型：创建时即写入套装，避免创建后再回头改。
  const [createMode, setCreateMode] = useState('bundle');
  const [step, setStep] = useState(1);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyList, setHistoryList] = useState<ComboKitSet[]>([]);
  const [watermark, setWatermark] = useState<ComboKitWatermark>(EMPTY_WATERMARK);
  const saveTimer = useRef<Record<string, number>>({});
  const refreshSeqRef = useRef(0);
  const openSetSeqRef = useRef(0);
  // 任务轮询序号：新动作会作废旧轮询，避免旧任务把已切换套装的结果写回界面。
  const pollSeqRef = useRef(0);
  // 选型默认辅助词来自 /roles；用 ref 读取可避免 openSet 因 roles 变化而重跑。
  const rolesRef = useRef<ComboRoles | null>(null);

  const notify = useCallback((ok: string) => { setMessage(ok); setError(''); }, []);
  const fail = useCallback((e: unknown) => { setError(e instanceof Error ? e.message : String(e)); setMessage(''); }, []);

  const refreshSet = useCallback(async (sid: string) => {
    const seq = ++refreshSeqRef.current;
    const data = await getSet(ctx, sid);
    if (seq !== refreshSeqRef.current) return;
    setSet(data);
    setForm((f) => ({
      ...f,
      name: data.name,
      sku: data.sku,
      sku_display: data.sku_display,
      description: data.description,
      category_path: data.category_path,
      category_id: data.category_id,
      declared_price: String(data.declared_price ?? ''),
      length_cm: String(data.length_cm ?? ''),
      width_cm: String(data.width_cm ?? ''),
      height_cm: String(data.height_cm ?? ''),
      weight_g: String(data.weight_g ?? ''),
      stock: String(data.stock ?? ''),
      category_name: String(data.category_name ?? ''),
      suggested_price_usd: String(data.suggested_price_usd ?? ''),
      id_type: String(data.id_type ?? ''),
      id_code: String(data.id_code ?? ''),
      spec: Array.isArray(data.sku_specs_json) ? (data.sku_specs_json as string[]).join(';') : '',
      generation_mode: data.generation_mode || 'bundle',
    }));
    setTextResult(data.text_result_json as Record<string, unknown> | null);
    setFusionPrompt(String(data.fusion_prompt || ''));
    setWatermark(readWatermark(data));
  }, [ctx]);

  useEffect(() => {
    if (!isActive) return;
    void getRoles(ctx).then((r) => { rolesRef.current = r; setRoles(r); }).catch(fail);
  }, [isActive, ctx, fail]);

  const openSet = useCallback(async (sid: string) => {
    const seq = ++openSetSeqRef.current;
    setLoading(true);
    try {
      await refreshSet(sid);
      if (seq !== openSetSeqRef.current) return;
      const full = await getSet(ctx, sid);
      if (seq !== openSetSeqRef.current) return;
      const mode = String(full.generation_mode || 'bundle');
      const p = (full.prompt || {}) as Record<string, unknown>;
      const storedBase = String(p.base_prompt_a || '').trim();
      const storedRoles = ((p.image_prompts as Record<string, string>) || {}) as Record<string, string>;
      const t = resolveActiveTemplate();
      const hasRoleContent = Object.values(storedRoles).some((v) => String(v).trim());
      // 若套装存的模板恰好等于某个内置预设：说明是「自动填充预设」，应在激活新预设时切换过去。
      const matchesBuiltin = COMBO_PRESET_TEMPLATES.some(
        (tpl) =>
          storedBase === tpl.base_prompt_a &&
          (storedRoles.carousel_2 || '') === tpl.role_directions.carousel_2 &&
          (storedRoles.carousel_3 || '') === tpl.role_directions.carousel_3 &&
          (storedRoles.white_bg || '') === tpl.role_directions.white_bg &&
          (storedRoles.detail_shot || '') === tpl.role_directions.detail_shot
      );
      const isCustomized = (storedBase || hasRoleContent) && !matchesBuiltin;
      if (isCustomized) {
        // 用户已自定义：保留，空角色用「当前激活预设」补齐，保证图片板块各辅助词可见。
        const mergedRoles: Record<string, string> = {
          ...(t.role_directions as Record<string, string>),
          ...Object.fromEntries(Object.entries(storedRoles).filter(([, v]) => String(v).trim())),
        };
        setBaseA(storedBase);
        setPrompts(mergedRoles);
      } else if (mode === 'multiview') {
        // 单品多视角：不套用「套装融合」预设文案（会误导生图），改用后端按选型下发的默认辅助词。
        const modeDefaults = (rolesRef.current?.default_image_prompts_by_mode?.multiview || {}) as Record<string, string>;
        setBaseA(storedBase);
        setPrompts({ ...modeDefaults });
        if (Object.keys(modeDefaults).length) {
          await savePrompt(ctx, sid, { base_prompt_a: storedBase, image_prompts: modeDefaults }).catch(() => undefined);
        }
      } else {
        // 全新套装 或 旧预设自动填充的套装：按「当前激活预设」填充，并保存到该套装，保证生成使用该模板。
        const imagePrompts = {
          carousel_2: t.role_directions.carousel_2,
          carousel_3: t.role_directions.carousel_3,
          white_bg: t.role_directions.white_bg,
          detail_shot: t.role_directions.detail_shot,
        };
        setBaseA(t.base_prompt_a);
        setPrompts(imagePrompts);
        await savePrompt(ctx, sid, { base_prompt_a: t.base_prompt_a, image_prompts: imagePrompts }).catch(() => undefined);
      }
      if (seq !== openSetSeqRef.current) return;
      setStep(1);
      setDrawerOpen(false);
      setSelectedItemId((full.items[0] as ComboKitItem | undefined)?.item_id ?? null);
    } catch (e) { fail(e); } finally { setLoading(false); }
  }, [ctx, refreshSet, fail]);

  useEffect(() => {
    if (initialSetId) void openSet(initialSetId);
  }, [initialSetId, openSet]);

  const createNewSet = async (mode = createMode) => {
    if (!createName.trim()) { fail('请填写套装名称'); return; }
    setBusy('create');
    try {
      const data = await createSet(ctx, { name: createName, sku: '', sku_display: '', description: '', category_path: '', category_id: '', specs: [], generation_mode: mode });
      setShowCreate(false);
      setCreateName('');
      await openSet(data.set_id);
      notify(mode === 'multiview' ? '套装已创建，请上传同一商品的 2~6 张视角图' : '套装已创建，请上传 2~6 张原图');
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const saveSet = async () => {
    if (!set) return;
    setBusy('save');
    try {
      const data = await updateSet(ctx, set.set_id, {
        name: form.name,
        sku: form.sku,
        sku_display: form.sku_display,
        description: form.description,
        category_path: form.category_path,
        category_id: form.category_id,
        declared_price: form.declared_price,
        length_cm: form.length_cm,
        width_cm: form.width_cm,
        height_cm: form.height_cm,
        weight_g: form.weight_g,
        stock: form.stock,
        category_name: form.category_name,
        suggested_price_usd: form.suggested_price_usd,
        id_type: form.id_type,
        id_code: form.id_code,
        fusion_prompt: fusionPrompt,
        generation_mode: form.generation_mode,
        specs: form.spec.split(';').map((s) => s.trim()).filter(Boolean),
      });
      // 一并保存 Prompt 基础模板 + 辅助词，避免只点「保存套装信息」导致刷新后丢失；
      // Prompt 单独保存失败不阻断套装信息主保存。
      try {
        await savePrompt(ctx, set.set_id, { base_prompt_a: baseA, image_prompts: prompts });
      } catch {
        /* 忽略 Prompt 保存失败 */
      }
      setSet(data);
      notify('套装信息已保存');
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  // 支持 FileList（input）与 File[]（剪贴板粘贴）两种来源。
  const onUpload = async (files: FileList | File[] | null) => {
    if (!set || !files?.length) return;
    const list = Array.from(files);
    setBusy('upload');
    try {
      for (const file of list) {
        await uploadItem(ctx, set.set_id, file);
      }
      notify(`已上传 ${list.length} 张原图`);
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  // 直接粘贴上传：从网站截图/复制图片后 Ctrl+V，剪贴板里的图片文件走上传同一条通道。
  useEffect(() => {
    if (!set) return undefined;
    const onPaste = (e: ClipboardEvent) => {
      const dt = e.clipboardData;
      if (!dt) return;
      const files = Array.from(dt.items)
        .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
        .map((item) => item.getAsFile())
        .filter((f): f is File => !!f);
      if (!files.length) return;
      e.preventDefault();
      void onUpload(files);
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [set, onUpload]);

  const onRemoveItem = async (itemId: string) => {
    if (!set) return;
    try {
      await removeItem(ctx, set.set_id, itemId);
      await refreshSet(set.set_id);
    } catch (e) { fail(e); }
  };

  useEffect(() => {
    return () => {
      Object.values(saveTimer.current).forEach((t) => window.clearTimeout(t));
      saveTimer.current = {};
    };
  }, []);

  const debouncedUpdate = (itemId: string, patch: Record<string, unknown>) => {
    const sid = set?.set_id;
    if (!sid) return;
    window.clearTimeout(saveTimer.current[itemId]);
    saveTimer.current[itemId] = window.setTimeout(() => {
      void updateItem(ctx, sid, itemId, patch).catch(() => {});
    }, 500);
  };

  const onItemKeyword = (itemId: string, value: string) => {
    setSet((s) => (s ? { ...s, items: s.items.map((it) => (it.item_id === itemId ? { ...it, subject_keywords: value } : it)) } : s));
    debouncedUpdate(itemId, { subject_keywords: value });
  };

  const onItemSpec = (itemId: string, value: string) => {
    setSet((s) => (s ? { ...s, items: s.items.map((it) => (it.item_id === itemId ? { ...it, spec_text: value } : it)) } : s));
    debouncedUpdate(itemId, { spec_text: value });
  };

  const onSaveMask = async (itemId: string, mask: { points: Array<[number, number]> }, inverted: boolean) => {
    try {
      await updateItem(ctx, set!.set_id, itemId, { mask: { points: mask.points }, mask_inverted: inverted, mask_edit: true });
      notify('蒙版已保存');
      await refreshSet(set!.set_id);
    } catch (e) { fail(e); }
  };

  // 算法预框选：后端本地分割出主体轮廓并已落库，这里只把点交给蒙版编辑器当初始框。
  // status 为 unavailable（未识别到主体）时返回 null，由 MaskCanvas 回落默认六边形。
  const onAutoMask = async (itemId: string): Promise<Array<[number, number]> | null> => {
    try {
      const res = await autoMaskItem(ctx, set!.set_id, itemId);
      if (res.status !== 'applied' || res.points.length < 3) return null;
      // 后端已写入 mask_json，静默刷新让列表数据与库一致；item_id 未变，蒙版编辑器本地点不会被重置。
      void refreshSet(set!.set_id).catch(() => {});
      return res.points;
    } catch { return null; }
  };

  const onReorder = async (order: string[]) => {
    if (!set) return;
    try { await reorderItems(ctx, set.set_id, order); await refreshSet(set.set_id); } catch (e) { fail(e); }
  };

  const onSetPrimary = async (itemId: string) => {
    if (!set) return;
    // 乐观更新：本地先把该成员标为主要、其余取消，再请求后端持久化（失败时刷新回滚）。
    setSet((s) => (s ? { ...s, items: s.items.map((it) => ({ ...it, is_primary: it.item_id === itemId })) } : s));
    try { await setPrimaryItem(ctx, set.set_id, itemId); }
    catch (e) { fail(e); await refreshSet(set.set_id).catch(() => undefined); }
  };

  // 轮询后台任务到终态：过程中把进度写进 progressText；
  // 返回 null 表示已被更新的动作取代（此时不得再改界面状态）。
  const pollTask = useCallback(
    async (sid: string, taskType: ComboKitTaskType, seq: number): Promise<ComboKitTask | null> => {
      const deadline = Date.now() + TASK_POLL_TIMEOUT_MS;
      for (;;) {
        const task = await getComboKitTask(ctx, sid, taskType);
        if (seq !== pollSeqRef.current) return null;
        if (task.status === 'completed' || task.status === 'failed') return task;
        const p = task.progress;
        setProgressText(p && p.total > 0 ? `${p.label} ${p.current}/${p.total}` : p?.label || '排队中…');
        if (Date.now() > deadline) throw new Error('任务仍在执行，请稍后打开该套装查看结果');
        await new Promise((resolve) => window.setTimeout(resolve, TASK_POLL_INTERVAL_MS));
      }
    },
    [ctx]
  );

  const onAnalyze = async () => {
    if (!set) return;
    setBusy('analyze');
    setProgressText('');
    const seq = ++pollSeqRef.current;
    try {
      if (!set.items.length) { fail('请先上传至少 2 张原图'); return; }
      const mode = form.generation_mode || 'bundle';
      // 单品多视角的每张图是一个视角而非成员商品，不强制填主体词。
      if (mode !== 'multiview') {
        const missing = set.items.filter((it) => !(it.subject_keywords || "").trim());
        if (missing.length) { fail('请为每个子商品填写主体词'); return; }
      }
      // 先把用户填写的融合主图提示词 + 模板 + 选型保存，再执行主体解析 + 主图生成。
      await updateSet(ctx, set.set_id, { fusion_prompt: fusionPrompt, generation_mode: mode });
      await savePrompt(ctx, set.set_id, { base_prompt_a: baseA, image_prompts: prompts }).catch(() => undefined);
      await startAnalyzeSubject(ctx, set.set_id);
      const task = await pollTask(set.set_id, 'subject', seq);
      if (!task) return;
      if (task.status === 'failed') { fail(task.error_message || '主体解析失败'); return; }
      notify(mode === 'multiview' ? '视角解析完成，已生成商品主图' : '主体解析完成，已生成融合主图');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); setProgressText(''); }
  };

  const onSavePrompt = async () => {
    if (!set) return;
    setBusy('savedPrompt');
    try {
      await savePrompt(ctx, set.set_id, { base_prompt_a: baseA, image_prompts: prompts });
      notify('模板配置已保存到该套装');
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onApplyPreset = async () => {
    if (!set) return;
    const t = resolveActiveTemplate();
    const imagePrompts = {
      carousel_2: t.role_directions.carousel_2,
      carousel_3: t.role_directions.carousel_3,
      white_bg: t.role_directions.white_bg,
      detail_shot: t.role_directions.detail_shot,
    };
    setBaseA(t.base_prompt_a);
    setPrompts(imagePrompts);
    setBusy('savedPrompt');
    try {
      await savePrompt(ctx, set.set_id, { base_prompt_a: t.base_prompt_a, image_prompts: imagePrompts });
      notify('已应用当前预设到该套装的图片模板');
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onGenerateText = async () => {
    if (!set) return;
    setBusy('text');
    setProgressText('');
    const seq = ++pollSeqRef.current;
    try {
      await startGenerateText(ctx, set.set_id);
      const task = await pollTask(set.set_id, 'text', seq);
      if (!task) return;
      if (task.status === 'failed') { fail(task.error_message || '文本生成失败'); return; }
      notify('文本已生成（扣 20 积分）');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); setProgressText(''); }
  };

  const onSaveWatermark = async () => {
    if (!set) return;
    setBusy('watermark');
    try {
      await updateSet(ctx, set.set_id, { watermark });
      // 保存后立即重烧已生成的成品图：不需要等下一次生成，也不重新生图/计费。
      const res = await applyWatermark(ctx, set.set_id);
      await refreshSet(set.set_id);
      const hasImages = (set.image_results_json || []).length > 0;
      if (watermark.enabled) {
        notify(hasImages ? `水印设置已保存，已应用到 ${res.applied} 张已生成的图` : '水印设置已保存，生成成品图时会自动带上');
      } else {
        notify(hasImages ? `已关闭水印，${res.applied} 张已生成的图已还原为无水印` : '已关闭水印');
      }
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onGenerateImages = async (roles?: string[]) => {
    if (!set) return;
    setBusy('images');
    setProgressText('');
    const seq = ++pollSeqRef.current;
    try {
      // 水印在生成时烧进图片：先把当前配置落盘，避免「改了水印直接点生成」仍用旧配置出图。
      await updateSet(ctx, set.set_id, { watermark });
      await startGenerateImages(ctx, set.set_id, roles);
      const task = await pollTask(set.set_id, 'image', seq);
      if (!task) return;
      if (task.status === 'failed') { fail(task.error_message || '成品图生成失败'); return; }
      notify(roles && roles.length ? `已重新生成 ${roles.length} 张图（扣 100 积分）` : '6 张成品图已生成（扣 100 积分）');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); setProgressText(''); }
  };

  const onDeleteImage = async (role: string) => {
    if (!set) return;
    try {
      await deleteGeneratedImage(ctx, set.set_id, role);
      notify('已删除该张成品图');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); }
  };

  const onSubmitPreview = async () => {
    if (!set) return;
    setBusy('preview');
    try {
      await createPreview(ctx, set.set_id);
      notify('已进入预检');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onReview = async (decision: 'pass' | 'reject', reason = '') => {
    if (!set) return;
    setBusy('review');
    try {
      await reviewPreview(ctx, set.set_id, { decision, reason });
      notify(decision === 'pass' ? '预检通过' : '已驳回');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onExportDianxiaomi = async () => {
    if (!set) return;
    setBusy('export');
    try {
      await exportComboDianxiaomi(ctx, set.set_id);
      notify('已导出店小秘导入模板');
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const openHistory = () => {
    setHistoryOpen(true);
    setDrawerOpen(false);
    void listSets(ctx).then((d) => setHistoryList(d.sets)).catch(fail);
  };

  const images = (set?.image_results_json || []) as Array<{ role: string; label: string; url: string; public_url?: string }>;
  const mainImage = images.find((img) => img.role === 'main');

  // 生成选型：以后端 /roles 下发为准，未加载时用本地兜底清单。
  const generationModes = roles?.generation_modes?.length ? roles.generation_modes : FALLBACK_GENERATION_MODES;
  const generationMode = form.generation_mode || set?.generation_mode || 'bundle';
  const isMultiview = generationMode === 'multiview';

  // 切换选型时同步给该选型配套的默认辅助词，避免沿用另一选型的文案。
  const onSelectMode = (mode: string) => {
    if (mode === generationMode) return;
    setForm((f) => ({ ...f, generation_mode: mode }));
    if (mode === 'multiview') {
      const modeDefaults = (roles?.default_image_prompts_by_mode?.multiview || {}) as Record<string, string>;
      if (Object.keys(modeDefaults).length) setPrompts({ ...modeDefaults });
    } else {
      const t = resolveActiveTemplate();
      setBaseA(t.base_prompt_a);
      setPrompts({
        carousel_2: t.role_directions.carousel_2,
        carousel_3: t.role_directions.carousel_3,
        white_bg: t.role_directions.white_bg,
        detail_shot: t.role_directions.detail_shot,
      });
    }
  };

  // 选型卡内的广告位轮播图：单品多视角放同一商品的多视角实拍，套装组合放成员商品合集。
  // 图片放在 public/assets/combo-kit/，按下方命名补齐文件即可自动多图轮播。
  const MODE_CARD_SLIDES: Record<string, ModeCardSlide[]> = {
    multiview: [
      { src: '/assets/combo-kit/single-1.png', caption: '闭合正面' },
      { src: '/assets/combo-kit/single-2.png', caption: '侧面视角' },
      { src: '/assets/combo-kit/single-3.png', caption: '开盖细节' },
    ],
    bundle: [
      { src: '/assets/combo-kit/bundle-1.png', caption: '成员商品' },
      { src: '/assets/combo-kit/bundle-2.png', caption: '居家场景' },
      { src: '/assets/combo-kit/bundle-3.png', caption: '组合全貌' },
      { src: '/assets/combo-kit/bundle-4.png', caption: '桌面搭配' },
    ],
  };

  const renderModeCards = (value: string, onChange: (mode: string) => void) => (
    <div className="combo-mode-cards" role="radiogroup" aria-label="生成选型">
      {generationModes.map((item) => {
        const active = value === item.mode;
        const slides = MODE_CARD_SLIDES[item.mode] || [];
        return (
          <div
            key={item.mode}
            role="radio"
            aria-checked={active}
            tabIndex={0}
            className={`combo-mode-card${active ? ' is-active' : ''}`}
            onClick={() => onChange(item.mode)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onChange(item.mode);
              }
            }}
          >
            {slides.length > 0 && (
              <span className="combo-mode-card-media">
                <ModeCardCarousel slides={slides} />
              </span>
            )}
            <span className="combo-mode-card-body">
              <span className="combo-mode-card-head">
                <span className="combo-mode-card-icon" aria-hidden="true">
                  <i className={`iconfont ${item.mode === 'multiview' ? 'icon-appstore' : 'icon-skin'}`} />
                </span>
                <strong>{item.label}</strong>
              </span>
              <small>{item.description}</small>
            </span>
            <span className="combo-mode-card-check" aria-hidden="true"><i className="iconfont icon-check-circle" /></span>
          </div>
        );
      })}
    </div>
  );

  const renderStep = () => {
    if (!set) return null;

    if (step === 1) {
      return (
        <section className="combo-section">
          <h2>① 套装信息与 SKU</h2>
          <h3 className="combo-subtitle">生成选型（决定生图方式，创建后仍可切换）</h3>
          {renderModeCards(generationMode, onSelectMode)}
          <div className="combo-hint">
            {isMultiview
              ? '单品多视角：全部原图视为同一商品的不同视角（正面/侧面/内部/包装展开图），不融合，直接生成商品主图。'
              : '套装组合：每张原图是一件成员商品，先融合成一张套装主图，再派生成品图。'}
          </div>
          <div className="combo-grid">
            <label>套装名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>SKU 货号<input value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></label>
          </div>
          <h3 className="combo-subtitle">店小秘导入必填字段</h3>
          <div className="combo-grid">
            <label>申报价格（店铺币种）<input value={form.declared_price} onChange={(e) => setForm({ ...form, declared_price: e.target.value })} placeholder="如 9.9" /></label>
            <label>长（cm）<input type="number" value={form.length_cm} onChange={(e) => setForm({ ...form, length_cm: e.target.value })} /></label>
            <label>宽（cm）<input type="number" value={form.width_cm} onChange={(e) => setForm({ ...form, width_cm: e.target.value })} /></label>
            <label>高（cm）<input type="number" value={form.height_cm} onChange={(e) => setForm({ ...form, height_cm: e.target.value })} /></label>
            <label>重量（g）<input type="number" value={form.weight_g} onChange={(e) => setForm({ ...form, weight_g: e.target.value })} /></label>
            <label>产品分类（必填，套装类目）
              <select value={form.category_name} onChange={(e) => setForm({ ...form, category_name: e.target.value })}>
                <option value="">— 请选择套装类目 —</option>
                {PRESET_SET_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <details className="combo-extra-fields">
            <summary>扩展字段（选填，不影响导出必填校验）</summary>
            <div className="combo-grid">
              <label>SKU 全称（可编辑）<input value={form.sku_display} onChange={(e) => setForm({ ...form, sku_display: e.target.value })} /></label>
              <label>各商品规格（;分隔）<input value={form.spec} onChange={(e) => setForm({ ...form, spec: e.target.value })} placeholder="暗黑版;透明版" /></label>
              <label>库存（件）<input type="number" value={form.stock} onChange={(e) => setForm({ ...form, stock: e.target.value })} /></label>
              <label>建议售价（USD）<input type="number" value={form.suggested_price_usd} onChange={(e) => setForm({ ...form, suggested_price_usd: e.target.value })} /></label>
              <label>识别码类型<input value={form.id_type} onChange={(e) => setForm({ ...form, id_type: e.target.value })} placeholder="如 UPC/EAN" /></label>
              <label>识别码<input value={form.id_code} onChange={(e) => setForm({ ...form, id_code: e.target.value })} /></label>
              <label>类目路径（选填）<input value={form.category_path} onChange={(e) => setForm({ ...form, category_path: e.target.value })} placeholder="可留空" /></label>
              <label>类目ID（选填）<input value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} placeholder="可留空" /></label>
            </div>
          </details>
          <div className="combo-actions"><button onClick={() => void saveSet()} disabled={busy === 'save'}>保存套装信息</button></div>
          <div className="combo-hint">SKU 规则：一套套装 = 单个独立 SKU；子商品仅作素材。导出店小秘需补齐必填项，成品图须已发布到 COS。</div>
        </section>
      );
    }

    if (step === 2) {
      const itemCount = set.items.length;
      const currentItem = set.items.find((it) => it.item_id === selectedItemId) ?? set.items[0];
      return (
        <section className="combo-section">
          <h2>② 上传原图（{itemCount}/{roles?.max_images ?? 6}）</h2>
          <div className="combo-actions">
            <label className="combo-upload">
              <input type="file" accept="image/*" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = ''; }} />
              {isMultiview ? '上传视角图' : '上传原图'}
            </label>
            <button className="primary" onClick={() => setDrawerOpen(true)} disabled={!set.items.length}>素材总览（选择图片）</button>
            <button onClick={() => onReorder(set.items.map((i) => i.item_id).slice().reverse())}>反转排序</button>
          </div>
          <div className="combo-paste-zone">
            <i className="iconfont icon-appstore" aria-hidden="true" />
            <span>也可直接复制网站图片或截图，在本页按 <kbd>Ctrl</kbd> + <kbd>V</kbd> 粘贴上传，一次可粘贴多张。</span>
          </div>
          <div className="combo-hint">
            {isMultiview
              ? '左侧大图为蒙版编辑器（内部视角/包装展开图建议保留整图不框选）；全部视角图总览在右侧弹窗，点选切换当前编辑的一张。'
              : '左侧大图绘制蒙版；所有图片的缩略图总览在右侧弹窗，点选切换当前编辑的图片。'}
          </div>
          {!currentItem && <div className="empty">请上传至少 2 张原图（{isMultiview ? '同一商品的多个视角' : '每张一件成员商品'}）。</div>}
          {currentItem && (
            <div className="combo-edit-stage">
              <div className="combo-edit-stage-head">
                <span className="combo-edit-stage-title">
                  正在编辑：{currentItem.subject_keywords || `第 ${set.items.findIndex((i) => i.item_id === currentItem.item_id) + 1} 张`}
                </span>
                <button className="btn-mini danger" onClick={() => void onRemoveItem(currentItem.item_id)}>移除</button>
              </div>
              <div className="combo-edit-stage-grid">
                <div className="combo-edit-stage-mask">
                  <MaskCanvas key={currentItem.item_id} setId={set.set_id} item={currentItem} onSaveMask={onSaveMask} onAutoMask={onAutoMask} />
                </div>
                <div className="combo-edit-stage-info">
                  {!isMultiview && (
                    <label className="combo-primary-toggle">
                      <input type="checkbox" checked={!!currentItem.is_primary} onChange={(e) => { if (e.target.checked) void onSetPrimary(currentItem.item_id); }} />
                      <span>设为套装主要商品（标题/主图主角）</span>
                    </label>
                  )}
                  <label>{isMultiview ? '视角说明' : '主体词'}<input value={currentItem.subject_keywords} onChange={(e) => onItemKeyword(currentItem.item_id, e.target.value)} placeholder={isMultiview ? '如：内部视角 / 包装展开图' : '如：手机壳'} /></label>
                  <label>规格<input value={currentItem.spec_text} onChange={(e) => onItemSpec(currentItem.item_id, e.target.value)} placeholder="如：暗黑版" /></label>
                  <button className="btn-mini primary" onClick={() => setDrawerOpen(true)}>切换其他图片</button>
                </div>
              </div>
            </div>
          )}
        </section>
      );
    }

    if (step === 3) {
      return (
        <section className="combo-section">
          <h2>{isMultiview ? '③ 商品主图' : '③ 融合套装主图'}</h2>
          <label>
            {isMultiview ? '商品主图补充要求（可选，英文更佳）' : '融合主图提示词（可选，英文更佳）'}
            <textarea
              rows={2}
              value={fusionPrompt}
              onChange={(e) => setFusionPrompt(e.target.value)}
              placeholder={isMultiview
                ? '例：keep the exact product structure shown in all views, clean studio background, soft light'
                : '例：a book and a brush pen holder on a wooden desk, soft studio light, clean neutral background'}
            />
          </label>
          <div className="combo-actions"><button onClick={() => void onAnalyze()} disabled={busy === 'analyze'}>{busy === 'analyze' ? (progressText || '解析生成中…') : (isMultiview ? '解析视角并生成商品主图' : '生成融合主图')}</button></div>
          {mainImage && (
            <div className="combo-fusion-preview">
              <h3>{isMultiview ? '商品主图（第 1 张成品图）' : '融合套装主图（第 1 张成品图）'}</h3>
              <img src={comboKitGeneratedUrl(set.set_id, mainImage.role)} alt={isMultiview ? '商品主图' : '融合主图'} referrerPolicy="no-referrer" />
            </div>
          )}
          <div className="combo-hint">
            {isMultiview
              ? '单品多视角：不做融合，以上传的全部视角图（含内部视角/包装展开图）为参考，直接生成商品主图。'
              : '套装组合：先按各成员主体词与蒙版抠出主体，再融合成一张套装主图。'}
          </div>
          <h3 className="combo-subtitle">图片模板（基础模板 A + 辅助词，由预设自动填充，可改）</h3>
          <div className="combo-grid">
            <label>基础模板 A<textarea rows={3} value={baseA} onChange={(e) => setBaseA(e.target.value)} /></label>
          </div>
          <div className="combo-grid">
            {(roles?.image_roles || []).filter((role) => (roles?.editable_prompt_roles ?? []).includes(role.role)).map((role: ComboImageRole) => (
              <label key={role.role}>
                {role.role === 'detail_shot' ? `${role.label}（补充要求，叠加在固定模板之上）` : role.label}
                <textarea rows={2} value={prompts[role.role] ?? ''} onChange={(e) => setPrompts({ ...prompts, [role.role]: e.target.value })} />
              </label>
            ))}
          </div>
          <div className="combo-actions">
            {!isMultiview && <button className="primary" onClick={() => void onApplyPreset()} disabled={busy === 'savedPrompt'}>应用当前预设</button>}
            <button className={isMultiview ? 'primary' : ''} onClick={() => void onSavePrompt()} disabled={busy === 'savedPrompt'}>保存模板到该套装</button>
          </div>
          <div className="combo-hint">使用场景图 1/2 与白底尺寸图使用上方辅助词；细节图以系统固定模板为底座，只追加补充要求；详情图本地拼接不开放自定义。</div>
          <h3 className="combo-subtitle">生成 6 张成品图</h3>
          <div className="combo-actions"><button className="primary" onClick={() => void onGenerateImages()} disabled={busy === 'images'}>{busy === 'images' ? (progressText || '并行生成中…') : '生成 6 张图（并行，扣 100 积分）'}</button></div>
          <div className="combo-hint">{isMultiview ? '主图复用商品主图；' : '主图复用融合主图；'}轮播 2/3、白底尺寸图、细节图并行生成；详情图本地拼接。</div>
        </section>
      );
    }

    if (step === 4) {
      return (
        <section className="combo-section">
          <h2>④ AI 文本生成（扣 20 积分）</h2>
          <div className="combo-actions"><button onClick={() => void onGenerateText()} disabled={busy === 'text'}>{busy === 'text' ? (progressText || '生成中…') : '生成标题+描述+五点'}</button></div>
          {textResult && (
            <div className="combo-text-result">
              <h3>{String(textResult.title ?? '')}</h3>
              <p>{String(textResult.description ?? '')}</p>
              <ul>{(textResult.bullets as string[] || []).map((b, i) => <li key={i}>{b}</li>)}</ul>
            </div>
          )}
        </section>
      );
    }

    if (step === 5) {
      return (
        <section className="combo-section">
          <h2>⑤ 生成 6 张成品图（4 次生图调用 · 扣 100 积分）+ 详情图</h2>
          <div className="combo-actions"><button className="primary" onClick={() => void onGenerateImages()} disabled={busy === 'images'}>{busy === 'images' ? (progressText || '并行生成中…') : '生成 6 张图（并行）'}</button></div>
          <div className="combo-hint">主图复用融合主图；使用场景图 1/2、白底尺寸图、细节图并行生成；详情图本地拼接。</div>
          <details className="combo-extra-fields combo-watermark">
            <summary>水印设置（可选 · 生成时烧进成品图，默认关闭）</summary>
            <label className="combo-primary-toggle">
              <input type="checkbox" checked={watermark.enabled} onChange={(e) => setWatermark({ ...watermark, enabled: e.target.checked })} />
              <span>在成品图上添加纯文字水印</span>
            </label>
            <div className="combo-grid">
              <label>水印文字<input value={watermark.text} maxLength={60} onChange={(e) => setWatermark({ ...watermark, text: e.target.value })} placeholder="如：店铺名 / 品牌名" /></label>
              <label>位置
                <select value={watermark.position} disabled={watermark.tile} onChange={(e) => setWatermark({ ...watermark, position: e.target.value as ComboKitWatermarkPosition })}>
                  {WATERMARK_POSITIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label>不透明度 {watermark.opacity}%<input type="range" min={0} max={100} step={5} value={watermark.opacity} onChange={(e) => setWatermark({ ...watermark, opacity: Number(e.target.value) })} /></label>
              <label>大小 {watermark.size}%（占图宽）<input type="range" min={1} max={30} step={1} value={watermark.size} onChange={(e) => setWatermark({ ...watermark, size: Number(e.target.value) })} /></label>
            </div>
            <label className="combo-primary-toggle">
              <input type="checkbox" checked={watermark.tile} onChange={(e) => setWatermark({ ...watermark, tile: e.target.checked })} />
              <span>平铺满图（整图重复铺开，开启后「位置」无效）</span>
            </label>
            <div className="combo-actions"><button onClick={() => void onSaveWatermark()} disabled={busy === 'watermark'}>{busy === 'watermark' ? '应用中…' : '保存并应用到已生成的图'}</button></div>
            <div className="combo-hint">水印直接烧进图片本身，页面预览 / 下载 / 导出店小秘 / 预检四处一致。保存后立即重烧已生成的成品图（不重新生图、不计费）；关闭水印保存则还原为干净图。详情图内部使用未加水印的主图，不会出现双层水印。</div>
          </details>
          <div className="combo-images">
            {images.map((img) => {
              const canRegenerate = REGENERATABLE_ROLES.includes(img.role);
              return (
                <figure key={img.role}>
                  <div className="combo-image-card">
                    <img src={comboKitGeneratedUrl(set.set_id, img.role)} alt={img.label} referrerPolicy="no-referrer" />
                    <div className="combo-image-actions">
                      {canRegenerate && <button className="btn-mini primary" onClick={() => void onGenerateImages([img.role])}>替换</button>}
                      <button className="btn-mini danger" onClick={() => void onDeleteImage(img.role)}>删除</button>
                    </div>
                  </div>
                  <figcaption>{img.label}</figcaption>
                </figure>
              );
            })}
            {!images.length && <div className="empty">尚未生成成品图。</div>}
          </div>
          {images.length > 0 && <div className="combo-hint">替换仅重做该张（生图角色，扣 100 积分），不会覆盖其它图；删除可将不满意的图移除。</div>}
        </section>
      );
    }

    return (
      <section className="combo-section">
        <h2>⑥ 独立预检</h2>

        {!set.preview ? (
          <div className="combo-actions"><button className="primary" onClick={() => void onSubmitPreview()} disabled={busy === 'preview'}>{busy === 'preview' ? '进入预检…' : '进入预检'}</button></div>
        ) : (
          <div className="combo-preview-review">
            <p className="combo-preview-status">预检状态：<strong>{String(set.preview.status)}</strong>{set.preview.reject_reason ? <em className="combo-preview-reason">驳回原因：{String(set.preview.reject_reason)}</em> : null}</p>
            {set.preview.status === 'rejected' || set.preview.status === 'pending' ? (
              <div className="combo-actions">
                <button className="primary" onClick={() => void onReview('pass')}>预检通过</button>
                <button className="danger" onClick={() => void onReview('reject', prompt('驳回原因') ?? '')}>驳回回退</button>
              </div>
            ) : null}
            {set.preview.status === 'passed' ? (
              <div className="combo-actions">
                <button className="primary" onClick={() => void onExportDianxiaomi()} disabled={busy === 'export'}>{busy === 'export' ? '过图床导出中…' : '过图床 · 导出店小秘'}</button>
              </div>
            ) : null}
          </div>
        )}

        <h3 className="combo-subtitle">套装信息</h3>
        <div className="combo-grid">
          <label>套装名称<b>{set.name || '—'}</b></label>
          <label>SKU 货号<b>{set.sku || '—'}</b></label>
          <label>SKU 全称<b>{set.sku_display || '—'}</b></label>
          <label>类目路径<b>{set.category_path || '—'}</b></label>
          <label>类目ID<b>{set.category_id || '—'}</b></label>
          <label>各商品规格<b>{Array.isArray(set.sku_specs_json) && set.sku_specs_json.length ? set.sku_specs_json.join(';') : '—'}</b></label>
        </div>

        <h3 className="combo-subtitle">店小秘必填项校验</h3>
        <div className="combo-grid">
          {[
            { k: 'declared_price', label: '申报价格', ok: !!(set.declared_price) },
            { k: 'length_cm', label: '长(cm)', ok: Number(set.length_cm) > 0 },
            { k: 'width_cm', label: '宽(cm)', ok: Number(set.width_cm) > 0 },
            { k: 'height_cm', label: '高(cm)', ok: Number(set.height_cm) > 0 },
            { k: 'weight_g', label: '重量(g)', ok: Number(set.weight_g) > 0 },
            { k: 'category_name', label: '产品分类', ok: !!set.category_name },
            { k: 'sku', label: 'SKU 货号', ok: !!(set.sku) },
          ].map((field) => (
            <span key={field.k} className={`combo-field-check ${field.ok ? 'is-ok' : 'is-missing'}`}>{field.ok ? '✓ ' : '✕ '}{field.label}</span>
          ))}
        </div>

        <h3 className="combo-subtitle">{isMultiview ? '视角原图' : '子商品原图'}</h3>
        <div className="combo-items">
          {set.items.length ? set.items.map((item, idx) => (
            <div className="combo-item" key={item.item_id}>
              <div className="combo-item-thumb">
                <img src={comboKitOriginUrl(set.set_id, (item.original_url || '').split('/').pop() || '')} alt={item.subject_keywords || '原图'} referrerPolicy="no-referrer" />
                <span className="idx">{idx + 1}</span>
              </div>
              <div className="combo-item-info"><b>主体：{item.subject_keywords || '未填'}</b><small>规格：{item.spec_text || '—'}</small></div>
            </div>
          )) : <div className="empty">未上传原图</div>}
        </div>

        <h3 className="combo-subtitle">成品图（图床直链校验）</h3>
        <div className="combo-images">
          {images.map((img) => (
            <figure key={img.role}>
              <div className="combo-image-card">
                <img src={comboKitGeneratedUrl(set.set_id, img.role)} alt={img.label} referrerPolicy="no-referrer" />
                <span className={`combo-cos-tag ${img.public_url ? 'is-ok' : 'is-missing'}`}>{img.public_url ? '已过图床' : '未过图床'}</span>
              </div>
              <figcaption>{img.label}</figcaption>
            </figure>
          ))}
          {!images.length && <div className="empty">尚未生成成品图</div>}
        </div>

        {textResult && (
          <>
            <h3 className="combo-subtitle">AI 文本结果</h3>
            <div className="combo-text-result">
              <h3>{String(textResult.title ?? '')}</h3>
              <p>{String(textResult.description ?? '')}</p>
              <ul>{(textResult.bullets as string[] || []).map((b, i) => <li key={i}>{b}</li>)}</ul>
            </div>
          </>
        )}

        {set.billing && set.billing.length > 0 && (
          <div className="combo-billing">
            <h3>扣费记录</h3>
            <ul>{set.billing.map((b) => <li key={b.billing_id}>{b.billing_type === 'text' ? '文本' : '生图'} · {b.points} 积分 · {b.status} · {b.result_status}</li>)}</ul>
          </div>
        )}
      </section>
    );
  };

  return (
    <div className={`combo-kit-page${set ? '' : ' is-empty'}`}>
      <header className="combo-kit-header">
        <div className="combo-kit-title">
          <span className="combo-kit-title-icon iconfont icon-skin" aria-hidden="true" />
          <div>
            <span>{isMultiview ? 'COMBO KIT · MULTIVIEW WORKFLOW' : 'COMBO KIT · FUSION WORKFLOW'}</span>
            <h1>{set ? set.name || '未命名套装' : '组合生图'}</h1>
            <p>{isMultiview
              ? '上传同一商品 2~6 张视角图（含内部/包装展开图）→ 解析视角 → 商品主图 → 文本+并行6图 → 独立预检'
              : '上传 2~6 张原图 → 逐图主体词/蒙版 → 融合主图 → 文本+并行6图 → 独立预检'}</p>
          </div>
        </div>
        <div className="combo-header-side">
          {set && (
            <div className="combo-header-stats">
              <span><i className="iconfont icon-appstore" aria-hidden="true" /><strong>{set.items.length}</strong><em>原图</em></span>
              <span><i className="iconfont icon-check-circle" aria-hidden="true" /><strong>{set.image_results_json.length}</strong><em>成品图</em></span>
            </div>
          )}
          <div className="combo-header-actions">
            <button onClick={openHistory}>历史</button>
            <button className="primary" onClick={() => { setShowCreate(true); setCreateName(''); setCreateMode('bundle'); }} disabled={busy === 'create'}>新建套装</button>
          </div>
        </div>
      </header>

      {(message || error) && <div className={`combo-kit-message ${error ? 'error' : ''}`}>{error || message}</div>}

      <main className="combo-kit-main">
        {!set && (
          <section className="combo-create-panel">
            <h2>新建组合套装</h2>
            <p>选择生成选型并填写套装名称即可开始：{isMultiview
              ? '上传同一商品的 2~6 张视角图 → 解析视角出商品主图 → AI 文本与成品图 → 独立预检导出。'
              : '上传 2~6 张原图 → 融合主图 → AI 文本与成品图 → 独立预检导出。'}</p>
            {renderModeCards(createMode, setCreateMode)}
            <input
              autoFocus
              placeholder="请输入套装名称"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void createNewSet(); }}
            />
            <div className="combo-modal-actions">
              <button onClick={openHistory} disabled={busy === 'create'}>历史套装</button>
              <button className="primary" onClick={() => void createNewSet()} disabled={busy === 'create'}>创建套装</button>
            </div>
          </section>
        )}
        {set && (
          <section className="combo-flow-card">
            <div className="combo-flow-heading">
              <span aria-hidden="true"><i className="iconfont icon-skin" /></span>
              <strong>{isMultiview ? '单品多视角工作流' : '组合套装工作流'}</strong>
              <small>{isMultiview
                ? '套装信息 → 上传视角图 → 商品主图 → AI 文本 → 成品图 → 预检'
                : '套装信息 → 上传原图 → 融合主图 → AI 文本 → 成品图 → 预检'}</small>
            </div>
            <ProductFlowSteps
              steps={buildFlowSteps(isMultiview)}
              activeId={String(step)}
              canOpen={COMBO_FLOW_ALWAYS_OPEN}
              onOpen={(id) => setStep(Number(id))}
              label="组合套装工作流"
            />
          </section>
        )}
        {set && <div className="combo-kit-content">{renderStep()}</div>}
      </main>

      {/* 上传原图右侧抽屉：逐张填信息与蒙版（portal 到 body 脱离 tab 面板层叠上下文，防被顶栏盖住） */}
      {drawerOpen && set && createPortal(
        <>
          <div className="combo-drawer-mask" onClick={() => setDrawerOpen(false)} />
          <aside className="combo-drawer">
            <div className="combo-drawer-head">
              <h3>素材总览（{set.items.length}）</h3>
              <button onClick={() => setDrawerOpen(false)}>关闭</button>
            </div>
            <label className="combo-upload">
              <input type="file" accept="image/*" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = ''; }} />
              继续上传原图
            </label>
            <div className="combo-drawer-grid">
              {set.items.map((item: ComboKitItem, idx) => (
                <div
                  key={item.item_id}
                  className={`combo-drawer-thumb${selectedItemId === item.item_id ? ' is-active' : ''}`}
                  onClick={() => { setSelectedItemId(item.item_id); setDrawerOpen(false); }}
                >
                  <img src={comboKitOriginUrl(set.set_id, (item.original_url || '').split('/').pop() || '')} alt={item.subject_keywords || '原图'} referrerPolicy="no-referrer" />
                  {item.is_primary && <span className="combo-drawer-thumb-primary">主要</span>}
                  <span className="combo-drawer-thumb-label">{idx + 1}. {item.subject_keywords || '未填主体词'}</span>
                  <button className="combo-drawer-thumb-remove" onClick={(e) => { e.stopPropagation(); void onRemoveItem(item.item_id); }}>移除</button>
                </div>
              ))}
            </div>
          </aside>
        </>, document.body)}

      {historyOpen && createPortal(
        <>
          <div className="combo-drawer-mask" onClick={() => setHistoryOpen(false)} />
          <aside className="combo-drawer">
            <div className="combo-drawer-head">
              <h3>历史组合套装</h3>
              <button onClick={() => setHistoryOpen(false)}>关闭</button>
            </div>
            <div className="combo-history-drawer-list">
              {historyList.map((s) => (
                <button key={s.set_id} type="button" className="combo-history-drawer-item" onClick={() => { void openSet(s.set_id); setHistoryOpen(false); }}>
                  <span className="combo-history-drawer-name">{s.name || '未命名套装'}</span>
                  <span className={`combo-status ${s.status}`}>{s.status}</span>
                </button>
              ))}
              {!historyList.length && <div className="combo-history-empty">暂无历史套装。</div>}
            </div>
          </aside>
        </>, document.body)}

      {showCreate && createPortal(
        <div className="combo-modal-mask" onClick={() => setShowCreate(false)}>
          <div className="combo-modal" onClick={(e) => e.stopPropagation()}>
            <h3>新建套装</h3>
            <input autoFocus placeholder="请输入套装名称" value={createName} onChange={(e) => setCreateName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void createNewSet(); }} />
            <div className="combo-create-mode">{renderModeCards(createMode, setCreateMode)}</div>
            <div className="combo-modal-actions">
              <button onClick={() => setShowCreate(false)}>取消</button>
              <button className="primary" onClick={() => void createNewSet()} disabled={busy === 'create'}>创建</button>
            </div>
          </div>
        </div>, document.body)}
    </div>
  );
}

export default ComboKitPage;
