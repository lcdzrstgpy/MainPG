import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAuthAccount, getAuthToken } from '../../../transport/http/client';
import type { ApiContext } from '../../product_processing/api/client';
import {
  analyzeSubject,
  comboKitGeneratedUrl,
  comboKitOriginUrl,
  createPreview,
  createSet,
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
  updateItem,
  updateSet,
  uploadItem,
  type ComboImageRole,
  type ComboKitItem,
  type ComboKitSet,
  type ComboRoles,
} from '../../product_processing/api/comboKitApi';
import { MaskCanvas } from '../components/MaskCanvas';
import '../styles/comboKit.css';

type Props = { isActive?: boolean };

function api(): ApiContext {
  const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
  return {
    baseUrl: '',
    token: getAuthToken(),
    workspaceId: account.workspace_id || account.workspace_code || 'default',
  };
}

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

export function ComboKitPage({ isActive = true }: Props) {
  const ctx = useMemo(() => api(), []);
  const [roles, setRoles] = useState<ComboRoles | null>(null);
  const [sets, setSets] = useState<ComboKitSet[]>([]);
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
  const saveTimer = useRef<Record<string, number>>({});

  const notify = useCallback((ok: string) => { setMessage(ok); setError(''); }, []);
  const fail = useCallback((e: unknown) => { setError(e instanceof Error ? e.message : String(e)); setMessage(''); }, []);

  const refreshSet = useCallback(async (sid: string) => {
    const data = await getSet(ctx, sid);
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
    }));
    setTextResult(data.text_result_json as Record<string, unknown> | null);
    setFusionPrompt(String(data.fusion_prompt || ''));
  }, [ctx]);

  useEffect(() => {
    if (!isActive) return;
    void getRoles(ctx).then(setRoles).catch(fail);
    void listSets(ctx).then((d) => setSets(d.sets)).catch(fail);
  }, [isActive, ctx, fail]);

  const openSet = useCallback(async (sid: string) => {
    setLoading(true);
    try {
      await refreshSet(sid);
      const full = await getSet(ctx, sid);
      const p = (full.prompt || {}) as Record<string, unknown>;
      setPrompts(((p.image_prompts as Record<string, string>) || {}) as Record<string, string>);
      setBaseA(String(p.base_prompt_a || ''));
    } catch (e) { fail(e); } finally { setLoading(false); }
  }, [ctx, refreshSet, fail]);

  const createNewSet = async () => {
    if (!createName.trim()) { fail('请填写套装名称'); return; }
    setBusy('create');
    try {
      const data = await createSet(ctx, { name: createName, sku: '', sku_display: '', description: '', category_path: '', category_id: '', specs: [] });
      setShowCreate(false);
      setCreateName('');
      setSets((p) => [data, ...p]);
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
      });
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

  const onAnalyze = async () => {
    if (!set) return;
    setBusy('analyze');
    try {
      if (!set.items.length) { fail('请先上传至少 2 张原图'); return; }
      const missing = set.items.filter((it) => !it.subject_keywords.trim());
      if (missing.length) { fail('请为每个子商品填写主体词'); return; }
      // 先把用户填写的融合主图提示词保存，再执行主体解析 + 融合主图生成。
      await updateSet(ctx, set.set_id, { fusion_prompt: fusionPrompt });
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
      notify('Prompt 配置已保存');
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

  const onGenerateImages = async () => {
    if (!set) return;
    setBusy('images');
    try {
      await generateImages(ctx, set.set_id);
      notify('6 张成品图已生成（扣 100 积分）');
      await refreshSet(set.set_id);
    } catch (e) { fail(e); } finally { setBusy(''); }
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

  const images = (set?.image_results_json || []) as Array<{ role: string; label: string; url: string }>;
  const mainImage = images.find((img) => img.role === 'main');

  return (
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <h1>商品组合套装</h1>
        <p>上传 2~6 张原图 → 逐图主体词/蒙版 → AI 解析 → 融合主图 → 套装信息 → Prompt → 文本+并行6图 → 独立预检</p>
      </header>

      {(message || error) && <div className={`combo-kit-message ${error ? 'error' : ''}`}>{error || message}</div>}

      <div className="combo-kit-body">
        <aside className="combo-kit-list">
          <h2>套装列表</h2>
          <button className="primary" onClick={() => { setShowCreate(true); setCreateName(''); }} disabled={busy === 'create'}>新建套装</button>
          <ul>
            {sets.map((s) => (
              <li key={s.set_id} className={set?.set_id === s.set_id ? 'active' : ''} onClick={() => void openSet(s.set_id)}>
                <strong>{s.name || '未命名套装'}</strong>
                <span className={`combo-status ${s.status}`}>{s.status}</span>
              </li>
            ))}
            {!sets.length && <li className="empty">暂无套装</li>}
          </ul>
        </aside>

        <main className="combo-kit-main">
          {!set && <div className="combo-kit-empty">点击左侧或新建套装开始。</div>}
          {set && (
            <>
              {/* Step 1 套装信息 */}
              <section className="combo-section">
                <h2>① 套装信息与 SKU</h2>
                <div className="combo-grid">
                  <label>套装名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
                  <label>SKU 货号<input value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></label>
                  <label>SKU 全称（可编辑）<input value={form.sku_display} onChange={(e) => setForm({ ...form, sku_display: e.target.value })} /></label>
                  <label>类目路径<input value={form.category_path} onChange={(e) => setForm({ ...form, category_path: e.target.value })} /></label>
                  <label>类目ID<input value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} /></label>
                  <label>各商品规格（;分隔）<input value={form.spec} onChange={(e) => setForm({ ...form, spec: e.target.value })} placeholder="暗黑版;透明版" /></label>
                </div>
                <textarea rows={3} placeholder="套装描述（未填 AI 生成）" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
                <h3 className="combo-subtitle">店小秘导入必填字段</h3>
                <div className="combo-grid">
                  <label>申报价格（店铺币种）<input value={form.declared_price} onChange={(e) => setForm({ ...form, declared_price: e.target.value })} placeholder="如 9.9" /></label>
                  <label>长（cm）<input type="number" value={form.length_cm} onChange={(e) => setForm({ ...form, length_cm: e.target.value })} /></label>
                  <label>宽（cm）<input type="number" value={form.width_cm} onChange={(e) => setForm({ ...form, width_cm: e.target.value })} /></label>
                  <label>高（cm）<input type="number" value={form.height_cm} onChange={(e) => setForm({ ...form, height_cm: e.target.value })} /></label>
                  <label>重量（g）<input type="number" value={form.weight_g} onChange={(e) => setForm({ ...form, weight_g: e.target.value })} /></label>
                  <label>库存（件）<input type="number" value={form.stock} onChange={(e) => setForm({ ...form, stock: e.target.value })} /></label>
                  <label>产品分类（必填）<input value={form.category_name} onChange={(e) => setForm({ ...form, category_name: e.target.value })} placeholder="如 办公文具" /></label>
                  <label>建议售价（USD）<input type="number" value={form.suggested_price_usd} onChange={(e) => setForm({ ...form, suggested_price_usd: e.target.value })} /></label>
                  <label>识别码类型<input value={form.id_type} onChange={(e) => setForm({ ...form, id_type: e.target.value })} placeholder="如 UPC/EAN" /></label>
                  <label>识别码<input value={form.id_code} onChange={(e) => setForm({ ...form, id_code: e.target.value })} /></label>
                </div>
                <div className="combo-actions"><button onClick={() => void saveSet()} disabled={busy === 'save'}>保存套装信息</button></div>
                <div className="combo-hint">SKU 规则：一套套装 = 单个独立 SKU；子商品仅作素材，不单独生成子 SKU。导出店小秘需补齐带 * 的必填项（申报价/长宽高/重量/分类），成品图须已发布到 COS 生成公网直链。</div>
              </section>

              {/* Step 2 原图 */}
              <section className="combo-section">
                <h2>② 上传原图（{set.items.length}/{roles?.max_images ?? 6}）</h2>
                <label className="combo-upload">
                  <input type="file" accept="image/*" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = ''; }} />
                  上传原图
                </label>
                <div className="combo-items">
                  {set.items.map((item, idx) => (
                    <div className="combo-item" key={item.item_id}>
                      <div className="combo-item-thumb">
                        <img src={comboKitOriginUrl(set.set_id, (item.original_url || '').split('/').pop() || '')} alt={item.subject_keywords || '原图'} referrerPolicy="no-referrer" />
                        <span className="idx">{idx + 1}</span>
                        <button className="btn-mini danger" onClick={() => void onRemoveItem(item.item_id)}>移除</button>
                      </div>
                      <input value={item.subject_keywords} onChange={(e) => onItemKeyword(item.item_id, e.target.value)} placeholder="主体词（如：手机壳）" />
                      <input value={item.spec_text} onChange={(e) => onItemSpec(item.item_id, e.target.value)} placeholder="规格（如：暗黑版）" />
                      <MaskCanvas setId={set.set_id} item={item} onSaveMask={onSaveMask} />
                    </div>
                  ))}
                  {!set.items.length && <div className="empty">请上传至少 2 张原图。</div>}
                </div>
                <div className="combo-grid"><label className="combo-grid-full">融合主图提示词（可选，英文更佳）<textarea rows={2} value={fusionPrompt} onChange={(e) => setFusionPrompt(e.target.value)} placeholder="例：a book and a brush pen holder on a wooden desk, soft studio light, clean neutral background" /></label></div>
                <div className="combo-actions"><button onClick={() => void onAnalyze()} disabled={busy === 'analyze'}>{busy === 'analyze' ? '解析中…' : 'AI 解析主体（生成主图）'}</button></div>
                <div className="combo-actions"><button onClick={() => onReorder(set.items.map((i) => i.item_id).slice().reverse())}>反转排序</button></div>
                {mainImage && (
                  <div className="combo-fusion-preview">
                    <h3>融合套装主图（第 1 张成品图）</h3>
                    <img src={comboKitGeneratedUrl(set.set_id, mainImage.role)} alt="融合主图" referrerPolicy="no-referrer" />
                  </div>
                )}
              </section>

              {/* Step 3 Prompt 配置 */}
              <section className="combo-section">
                <h2>③ Prompt 配置（基础模板 A + 3 项可编辑辅助词）</h2>
                <div className="combo-grid">
                  <label>基础模板 A<textarea rows={3} value={baseA} onChange={(e) => setBaseA(e.target.value)} /></label>
                </div>
                <div className="combo-grid">
                  {(roles?.image_roles || []).filter((role) => (roles?.editable_prompt_roles ?? []).includes(role.role)).map((role: ComboImageRole) => (
                    <label key={role.role}>{role.label}<textarea rows={2} value={prompts[role.role] ?? ''} onChange={(e) => setPrompts({ ...prompts, [role.role]: e.target.value })} /></label>
                  ))}
                </div>
                <div className="combo-hint">套装主图在 AI 解析主体阶段生成；细节图、详情图复用固定模板，不开放自定义提示词。</div>
                <div className="combo-actions"><button onClick={() => void onSavePrompt()} disabled={busy === 'savedPrompt'}>保存 Prompt</button></div>
              </section>

              {/* Step 4 文本生成 */}
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

              {/* Step 5 成品图生成 */}
              <section className="combo-section">
                <h2>⑤ 生成 6 张成品图（4 次生图调用 · 扣 100 积分）</h2>
                <div className="combo-actions"><button className="primary" onClick={() => void onGenerateImages()} disabled={busy === 'images'}>{busy === 'images' ? '并行生成中…' : '生成 6 张图（并行）'}</button></div>
                <div className="combo-hint">主图复用融合图；轮播 2/3、白底尺寸图、细节图并行生成；详情图本地拼接。</div>
                <div className="combo-images">
                  {images.map((img) => (
                    <figure key={img.role}>
                      <img src={comboKitGeneratedUrl(set.set_id, img.role)} alt={img.label} referrerPolicy="no-referrer" />
                      <figcaption>{img.label}</figcaption>
                    </figure>
                  ))}
                  {!images.length && <div className="empty">尚未生成成品图。</div>}
                </div>
              </section>

              {/* Step 6 独立预检 */}
              <section className="combo-section">
                <h2>⑥ 独立预检</h2>
                {set.preview ? (
                  <div className="combo-preview-review">
                    <p>当前状态：<strong>{String(set.preview.status)}</strong></p>
                    {set.preview.reject_reason ? <p>驳回原因：{String(set.preview.reject_reason)}</p> : null}
                    {set.preview.status === 'rejected' || set.preview.status === 'pending' ? (
                      <div className="combo-actions">
                        <button className="primary" onClick={() => void onReview('pass')}>预检通过</button>
                        <button className="danger" onClick={() => void onReview('reject', prompt('驳回原因') ?? '')}>驳回回退</button>
                      </div>
                    ) : null}
                    {set.preview.status === 'passed' ? (
                      <div className="combo-actions">
                        <button className="primary" onClick={() => void onExportDianxiaomi()} disabled={busy === 'export'}>{busy === 'export' ? '导出中…' : '导出店小秘'}</button>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="combo-actions"><button onClick={() => void onSubmitPreview()} disabled={busy === 'preview'}>进入预检</button></div>
                )}
                {set.billing && set.billing.length > 0 && (
                  <div className="combo-billing">
                    <h3>扣费记录</h3>
                    <ul>{set.billing.map((b) => <li key={b.billing_id}>{b.billing_type === 'text' ? '文本' : '生图'} · {b.points} 积分 · {b.status} · {b.result_status}</li>)}</ul>
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </div>

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
