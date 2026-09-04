import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAuthAccount, getAuthToken } from '../../../transport/http/client';
import type { ApiContext } from '../../product_processing/api/client';
import {
  analyzeSubject,
  comboKitGeneratedUrl,
  comboKitOriginUrl,
  createPreview,
  createSet,
  deleteGeneratedImage,
  exportComboDianxiaomi,
  generateImages,
  generateText,
  getRoles,
  getSet,
  listSets,
  reorderItems,
  removeItem,
  reviewPreview,
  savePrompt,
  setPrimaryItem,
  updateItem,
  updateSet,
  uploadItem,
  type ComboImageRole,
  type ComboKitItem,
  type ComboKitSet,
  type ComboRoles,
} from '../../product_processing/api/comboKitApi';
import { MaskCanvas } from '../components/MaskCanvas';
import { COMBO_PRESET_TEMPLATES, resolveActiveTemplate } from '../presetTemplates';
import '../styles/comboKit.css';

type Props = { isActive?: boolean; initialSetId?: string };

const COMBO_STEPS = [
  { key: 1, label: '① 套装信息' },
  { key: 2, label: '② 上传原图' },
  { key: 3, label: '③ 融合主图' },
  { key: 4, label: '④ AI 文本' },
  { key: 5, label: '⑤ 成品图' },
  { key: 6, label: '⑥ 预检' },
];

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
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [baseA, setBaseA] = useState('');
  const [fusionPrompt, setFusionPrompt] = useState('');
  const [textResult, setTextResult] = useState<Record<string, unknown> | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [step, setStep] = useState(1);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyList, setHistoryList] = useState<ComboKitSet[]>([]);
  const saveTimer = useRef<Record<string, number>>({});
  const refreshSeqRef = useRef(0);
  const openSetSeqRef = useRef(0);

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
    }));
    setTextResult(data.text_result_json as Record<string, unknown> | null);
    setFusionPrompt(String(data.fusion_prompt || ''));
  }, [ctx]);

  useEffect(() => {
    if (!isActive) return;
    void getRoles(ctx).then(setRoles).catch(fail);
  }, [isActive, ctx, fail]);

  const openSet = useCallback(async (sid: string) => {
    const seq = ++openSetSeqRef.current;
    setLoading(true);
    try {
      await refreshSet(sid);
      if (seq !== openSetSeqRef.current) return;
      const full = await getSet(ctx, sid);
      if (seq !== openSetSeqRef.current) return;
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
          (storedRoles.white_bg || '') === tpl.role_directions.white_bg
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
      } else {
        // 全新套装 或 旧预设自动填充的套装：按「当前激活预设」填充，并保存到该套装，保证生成使用该模板。
        const imagePrompts = {
          carousel_2: t.role_directions.carousel_2,
          carousel_3: t.role_directions.carousel_3,
          white_bg: t.role_directions.white_bg,
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

  const createNewSet = async () => {
    if (!createName.trim()) { fail('请填写套装名称'); return; }
    setBusy('create');
    try {
      const data = await createSet(ctx, { name: createName, sku: '', sku_display: '', description: '', category_path: '', category_id: '', specs: [] });
      setShowCreate(false);
      setCreateName('');
      await openSet(data.set_id);
      notify('套装已创建，请上传 2~6 张原图');
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

  const onUpload = async (files: FileList | null) => {
    if (!set || !files?.length) return;
    setBusy('upload');
    try {
      for (const file of Array.from(files)) {
        await uploadItem(ctx, set.set_id, file);
      }
      notify(`已上传 ${files.length} 张原图`);
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

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

  const onAnalyze = async () => {
    if (!set) return;
    setBusy('analyze');
    try {
      if (!set.items.length) { fail('请先上传至少 2 张原图'); return; }
      const missing = set.items.filter((it) => !it.subject_keywords.trim());
      if (missing.length) { fail('请为每个子商品填写主体词'); return; }
      // 先把用户填写的融合主图提示词 + 模板保存，再执行主体解析 + 融合主图生成。
      await updateSet(ctx, set.set_id, { fusion_prompt: fusionPrompt });
      await savePrompt(ctx, set.set_id, { base_prompt_a: baseA, image_prompts: prompts }).catch(() => undefined);
      await analyzeSubject(ctx, set.set_id);
      notify('主体解析完成，已生成融合主图');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
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
    try {
      const r = await generateText(ctx, set.set_id);
      setTextResult(r as unknown as Record<string, unknown>);
      notify('文本已生成（扣 20 积分）');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
  };

  const onGenerateImages = async (roles?: string[]) => {
    if (!set) return;
    setBusy('images');
    try {
      await generateImages(ctx, set.set_id, roles);
      notify(roles && roles.length ? `已重新生成 ${roles.length} 张图（扣 100 积分）` : '6 张成品图已生成（扣 100 积分）');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
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

  const renderStep = () => {
    if (!set) return null;

    if (step === 1) {
      return (
        <section className="combo-section">
          <h2>① 套装信息与 SKU</h2>
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
              上传原图
            </label>
            <button className="primary" onClick={() => setDrawerOpen(true)} disabled={!set.items.length}>素材总览（选择图片）</button>
            <button onClick={() => onReorder(set.items.map((i) => i.item_id).slice().reverse())}>反转排序</button>
          </div>
          <div className="combo-hint">左侧大图绘制蒙版；所有图片的缩略图总览在右侧弹窗，点选切换当前编辑的图片。</div>
          {!currentItem && <div className="empty">请上传至少 2 张原图。</div>}
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
                  <MaskCanvas key={currentItem.item_id} setId={set.set_id} item={currentItem} onSaveMask={onSaveMask} />
                </div>
                <div className="combo-edit-stage-info">
                  <label className="combo-primary-toggle">
                    <input type="checkbox" checked={!!currentItem.is_primary} onChange={(e) => { if (e.target.checked) void onSetPrimary(currentItem.item_id); }} />
                    <span>设为套装主要商品（标题/主图主角）</span>
                  </label>
                  <label>主体词<input value={currentItem.subject_keywords} onChange={(e) => onItemKeyword(currentItem.item_id, e.target.value)} placeholder="如：手机壳" /></label>
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
          <h2>③ 融合套装主图</h2>
          <label>融合主图提示词（可选，英文更佳）<textarea rows={2} value={fusionPrompt} onChange={(e) => setFusionPrompt(e.target.value)} placeholder="例：a book and a brush pen holder on a wooden desk, soft studio light, clean neutral background" /></label>
          <div className="combo-actions"><button onClick={() => void onAnalyze()} disabled={busy === 'analyze'}>{busy === 'analyze' ? '解析生成中…' : '生成融合主图'}</button></div>
          {mainImage && (
            <div className="combo-fusion-preview">
              <h3>融合套装主图（第 1 张成品图）</h3>
              <img src={comboKitGeneratedUrl(set.set_id, mainImage.role)} alt="融合主图" referrerPolicy="no-referrer" />
            </div>
          )}
          <h3 className="combo-subtitle">图片模板（基础模板 A + 辅助词，由预设自动填充，可改）</h3>
          <div className="combo-grid">
            <label>基础模板 A<textarea rows={3} value={baseA} onChange={(e) => setBaseA(e.target.value)} /></label>
          </div>
          <div className="combo-grid">
            {(roles?.image_roles || []).filter((role) => (roles?.editable_prompt_roles ?? []).includes(role.role)).map((role: ComboImageRole) => (
              <label key={role.role}>{role.label}<textarea rows={2} value={prompts[role.role] ?? ''} onChange={(e) => setPrompts({ ...prompts, [role.role]: e.target.value })} /></label>
            ))}
          </div>
          <div className="combo-actions">
            <button className="primary" onClick={() => void onApplyPreset()} disabled={busy === 'savedPrompt'}>应用当前预设</button>
            <button onClick={() => void onSavePrompt()} disabled={busy === 'savedPrompt'}>保存模板到该套装</button>
          </div>
          <div className="combo-hint">细节图、详情图复用固定模板，不开放自定义提示词。可在「提示词模板预设」切换全局默认模板。</div>
          <h3 className="combo-subtitle">生成 6 张成品图</h3>
          <div className="combo-actions"><button className="primary" onClick={() => void onGenerateImages()} disabled={busy === 'images'}>{busy === 'images' ? '并行生成中…' : '生成 6 张图（并行，扣 100 积分）'}</button></div>
          <div className="combo-hint">主图复用融合主图；轮播 2/3、白底尺寸图、细节图并行生成；详情图本地拼接。</div>
        </section>
      );
    }

    if (step === 4) {
      return (
        <section className="combo-section">
          <h2>④ AI 文本生成（扣 20 积分）</h2>
          <div className="combo-actions"><button onClick={() => void onGenerateText()} disabled={busy === 'text'}>{busy === 'text' ? '生成中…' : '生成标题+描述+五点'}</button></div>
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
          <div className="combo-actions"><button className="primary" onClick={() => void onGenerateImages()} disabled={busy === 'images'}>{busy === 'images' ? '并行生成中…' : '生成 6 张图（并行）'}</button></div>
          <div className="combo-hint">主图复用融合图；轮播 2/3、白底尺寸图、细节图并行生成；详情图本地拼接。</div>
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

        <h3 className="combo-subtitle">子商品原图</h3>
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
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <div>
          <h1>{set ? set.name || '未命名套装' : '组合生图'}</h1>
          <p>上传 2~6 张原图 → 逐图主体词/蒙版 → 融合主图 → 文本+并行6图 → 独立预检</p>
        </div>
        <div className="combo-header-actions">
          <button onClick={openHistory}>历史</button>
          <button className="primary" onClick={() => { setShowCreate(true); setCreateName(''); }} disabled={busy === 'create'}>新建套装</button>
        </div>
      </header>

      {(message || error) && <div className={`combo-kit-message ${error ? 'error' : ''}`}>{error || message}</div>}

      <main className="combo-kit-main">
        {!set && <div className="combo-kit-empty">点击「新建套装」开始创建组合套装。</div>}
        {set && (
          <div className="combo-steps">
            {COMBO_STEPS.map((s) => (
              <button key={s.key} type="button" className={`combo-step-tab${step === s.key ? ' is-active' : ''}`} onClick={() => setStep(s.key)}>{s.label}</button>
            ))}
          </div>
        )}
        {set && <div className="combo-kit-content">{renderStep()}</div>}
      </main>

      {/* 上传原图右侧抽屉：逐张填信息与蒙版 */}
      {drawerOpen && set && (
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
        </>
      )}

      {historyOpen && (
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
        </>
      )}

      {showCreate && (
        <div className="combo-modal-mask" onClick={() => setShowCreate(false)}>
          <div className="combo-modal" onClick={(e) => e.stopPropagation()}>
            <h3>新建套装</h3>
            <input autoFocus placeholder="请输入套装名称" value={createName} onChange={(e) => setCreateName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void createNewSet(); }} />
            <div className="combo-modal-actions">
              <button onClick={() => setShowCreate(false)}>取消</button>
              <button className="primary" onClick={() => void createNewSet()} disabled={busy === 'create'}>创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ComboKitPage;
