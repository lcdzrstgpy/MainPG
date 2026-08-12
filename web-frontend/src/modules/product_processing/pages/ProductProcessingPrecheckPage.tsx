import { useEffect, useRef, useState } from 'react';
import { ppDownload, ppRequest, ppUpload, type ApiContext } from '../api/client';
import type {
  PreviewCoreFields,
  PreviewExportResponse,
  PreviewItem,
  PreviewOverrides,
  PreviewResponse,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';
import { DimensionChangeSetReview } from '../components/DimensionChangeSetReview';

const API_BASE = '/api/product-processing';

type Props = {
  taskId: number;
  initialChangeSetId?: string;
  onOpenDimensionItem: (taskId: number, taskItemId: number) => void;
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
    partial_failure: '部分完成',
    failed: '任务失败',
  })[status] || status;
}

/** 编辑缓冲：仅记录与「生成结果默认值」不同的覆盖项；未编辑的字段回落到默认值 */
type ItemEdits = {
  title?: string;
  description?: string;
  main_image?: string;
  carousel_images?: string[];
  detail_images?: string[];
  core_fields?: PreviewCoreFields;
};

export function ProductProcessingPrecheckPage({ taskId, initialChangeSetId, onOpenDimensionItem }: Props) {
  const ctx = api();
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [edits, setEdits] = useState<Record<number, ItemEdits>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [activeImage, setActiveImage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileTarget, setFileTarget] = useState<{ draftId: number; kind: 'main' | 'carousel' | 'detail' } | null>(null);

  const notify = (ok: string) => { setMessage(ok); setError(''); };
  const fail = (err: unknown) => { setError(err instanceof Error ? err.message : String(err)); setMessage(''); };

  const load = async () => {
    setLoading(true);
    try {
      const data = await ppRequest<PreviewResponse>(ctx, `${API_BASE}/tasks/${taskId}/preview`);
      setPreview(data);
      setEdits({});
    } catch (err) { fail(err); } finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  if (!preview) {
    return (
      <div className="verify-page">
        <header className="verify-commandbar">
          <div className="verify-command-title">
            <span className="verify-eyebrow">PRODUCT PROCESSING · 预检</span>
            <h1>预检</h1>
          </div>
        </header>
        {(message || error) && <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>}
        <p className="verify-empty">{loading ? '加载预检数据…' : '任务尚未完成，无法预检'}</p>
      </div>
    );
  }

  const editFor = (item: PreviewItem): ItemEdits => edits[item.product_draft_id ?? item.item_id] ?? {};
  const setEdit = (draftId: number, patch: Partial<ItemEdits>) => {
    setEdits((prev) => ({ ...prev, [draftId]: { ...(prev[draftId] ?? {}), ...patch } }));
  };

  const effective = (item: PreviewItem) => {
    const edit = editFor(item);
    const carousel = edit.carousel_images ?? item.carousel_images;
    const detail = edit.detail_images ?? item.detail_images;
    return {
      title: edit.title ?? item.title,
      description: edit.description ?? item.description,
      main_image: edit.main_image ?? carousel[0] ?? item.main_image,
      carousel,
      detail,
    };
  };

  /** 收集覆盖：与生成结果默认值不同的项才写入，保证「不修改默认保存」不产生冗余覆盖 */
  const collectOverrides = (item: PreviewItem): PreviewOverrides => {
    const edit = editFor(item);
    const overrides: PreviewOverrides = {};
    if (edit.title !== undefined && edit.title !== item.title) overrides.title = edit.title;
    if (edit.description !== undefined && edit.description !== item.description) overrides.description = edit.description;
    if (edit.main_image !== undefined && edit.main_image !== (item.carousel_images[0] ?? item.main_image)) overrides.main_image = edit.main_image;
    if (edit.carousel_images !== undefined) {
      const base = item.carousel_images;
      if (edit.carousel_images.length !== base.length || edit.carousel_images.some((value, index) => value !== base[index])) {
        overrides.carousel_images = edit.carousel_images;
      }
    }
    if (edit.detail_images !== undefined) {
      const base = item.detail_images;
      if (edit.detail_images.length !== base.length || edit.detail_images.some((value, index) => value !== base[index])) {
        overrides.detail_images = edit.detail_images;
      }
    }
    if (edit.core_fields) {
      const core: Record<string, unknown> = {};
      for (const key of Object.keys(edit.core_fields)) {
        const next = edit.core_fields[key as keyof PreviewCoreFields];
        const baseValue = item.core_fields[key as keyof PreviewCoreFields];
        const normalized = (value: unknown) => (typeof value === 'string' ? value.trim() : value);
        if (normalized(next) !== normalized(baseValue)) core[key] = next;
      }
      if (Object.keys(core).length > 0) overrides.core_fields = core as PreviewCoreFields;
    }
    return overrides;
  };

  const saveAll = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const items = preview.items
        .filter((item) => item.product_draft_id != null)
        .map((item) => ({ product_draft_id: item.product_draft_id!, overrides: collectOverrides(item) }));
      const data = await ppRequest<{ saved_count: number }>(ctx, `${API_BASE}/tasks/${taskId}/preview`, {
        method: 'PATCH',
        body: { items },
      });
      notify(`已保存 ${data.saved_count ?? items.length} 条商品的预检修改`);
      await load();
    } catch (err) { fail(err); } finally { setSaving(false); }
  };

  const exportFinal = async () => {
    setExporting(true);
    setError('');
    setMessage('');
    try {
      const data = await ppRequest<PreviewExportResponse>(ctx, `${API_BASE}/tasks/${taskId}/preview/export`, {
        method: 'POST',
        body: {},
      });
      notify(`已导出最终版表格（${data.product_count} 个商品 / ${data.row_count} 行），开始下载…`);
      await ppDownload(ctx, `${API_BASE}/tasks/${taskId}/download?kind=dxm_final`, data.file || `dxm_import_task_${taskId}_final.xlsx`);
    } catch (err) { fail(err); } finally { setExporting(false); }
  };

  const openFilePicker = (draftId: number, kind: 'main' | 'carousel' | 'detail') => {
    setFileTarget({ draftId, kind });
    if (fileInputRef.current) fileInputRef.current.click();
  };

  const handleFile = async (file: File | null) => {
    if (!file || !fileTarget) return;
    const { draftId, kind } = fileTarget;
    const formData = new FormData();
    formData.append('draft_id', String(draftId));
    formData.append('image_file', file);
    try {
      const data = await ppUpload<{ url: string }>(ctx, `${API_BASE}/tasks/${taskId}/preview/images`, formData);
      const item = preview.items.find((it) => (it.product_draft_id ?? it.item_id) === draftId);
      if (!item) return;
      const current = effective(item);
      if (kind === 'main') {
        setEdit(draftId, { main_image: data.url });
      } else if (kind === 'carousel') {
        const index = current.carousel.indexOf(current.main_image);
        const next = [...current.carousel];
        next[index >= 0 ? index : next.length] = data.url;
        setEdit(draftId, { carousel_images: next });
      } else {
        setEdit(draftId, { detail_images: [...current.detail, data.url] });
      }
      notify('图片已上传并替换');
    } catch (err) { fail(err); } finally { setFileTarget(null); }
  };

  const removeImage = (draftId: number, kind: 'carousel' | 'detail', index: number) => {
    const item = preview.items.find((it) => (it.product_draft_id ?? it.item_id) === draftId);
    if (!item) return;
    const current = effective(item);
    if (kind === 'carousel') {
      const next = current.carousel.filter((_, i) => i !== index);
      setEdit(draftId, { carousel_images: next });
    } else {
      setEdit(draftId, { detail_images: current.detail.filter((_, i) => i !== index) });
    }
  };

  const setCarouselAsMain = (draftId: number, url: string) => {
    setEdit(draftId, { main_image: url });
    notify('已设为主图（预览图/素材图）');
  };

  const setField = (draftId: number, key: keyof PreviewCoreFields, value: string) => {
    const item = preview.items.find((it) => (it.product_draft_id ?? it.item_id) === draftId);
    if (!item) return;
    const current = editFor(item);
    const base = item.core_fields[key];
    const isNumeric = ['declared_price', 'suggested_price', 'stock', 'length_cm', 'width_cm', 'height_cm', 'weight_g'].includes(key);
    const parsed: string | number | null = isNumeric && value.trim() !== '' ? Number(value) : value;
    const nextCore: PreviewCoreFields = { ...(current.core_fields ?? {}) };
    if (parsed !== base) nextCore[key] = parsed as never;
    else delete nextCore[key];
    setEdit(draftId, { core_fields: nextCore });
  };

  const dirtyCount = preview.items.filter((item) => Object.keys(collectOverrides(item)).length > 0).length;

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <span className="verify-eyebrow">PRODUCT PROCESSING · 预检环节</span>
          <h1>预检与导出最终版</h1>
          <p>逐条核对标题、原图、生成图轮播、详情图与核心字段；可直接修改或默认保存，最后导出最终版表格导入店小秘（字段规则与原版一致）。</p>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      {initialChangeSetId && <DimensionChangeSetReview changeSetId={initialChangeSetId} onChanged={load} />}

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>任务 #{preview.task_id} · {preview.task.title}</h2>
          <span className="verify-sub">{taskStatusLabel(preview.task.status)} · 共 {preview.task.total_count} 项 · 成功 {preview.task.success_count} · 失败 {preview.task.failed_count}</span>
        </div>
        <div className="verify-actions">
          <button className="primary" onClick={saveAll} disabled={saving || loading || preview.items.length === 0}>
            {saving ? '保存中…' : `保存预检修改${dirtyCount > 0 ? `（${dirtyCount} 条有修改）` : ''}`}
          </button>
          <button onClick={exportFinal} disabled={exporting || loading || preview.items.length === 0}>
            {exporting ? '导出中…' : '导出最终版表格'}
          </button>
          <button onClick={load} disabled={loading}>重新加载</button>
        </div>
      </section>

      {preview.items.length === 0 && <p className="verify-empty">任务没有可预检的成功商品。</p>}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(e) => { handleFile(e.target.files?.[0] ?? null); e.target.value = ''; }}
      />

      {preview.items.map((item) => {
        const view = effective(item);
        const edit = editFor(item);
        const draftId = item.product_draft_id ?? item.item_id;
        const hasOverrides = Object.keys(collectOverrides(item)).length > 0;
        return (
          <section key={item.item_id} className={`verify-section precheck-card${hasOverrides ? ' is-edited' : ''}`}>
            <div className="verify-section-head">
              <h2>{item.skc || `商品 #${draftId}`}</h2>
              <span className="verify-sub">{hasOverrides ? '已修改' : '未修改'} · 状态 {item.status}</span>
            </div>

            <div className="precheck-grid">
              {/* 左列：标题 / 描述 / 核心字段 */}
              <div className="precheck-fields">
                <label className="precheck-label">
                  <span>标题（导出 *产品标题 / *英文标题）</span>
                  <textarea
                    rows={3}
                    value={view.title}
                    onChange={(e) => setEdit(draftId, { title: e.target.value })}
                    placeholder="AI 生成标题"
                  />
                </label>
                <label className="precheck-label">
                  <span>产品描述（导出产品描述列，详情图自动追加）</span>
                  <textarea
                    rows={5}
                    value={view.description}
                    onChange={(e) => setEdit(draftId, { description: e.target.value })}
                    placeholder="AI 生成五点描述"
                  />
                </label>
                <div className="precheck-core-grid">
                  <label>SKU货号
                    <input value={edit.core_fields?.sku ?? item.core_fields.sku ?? ''} onChange={(e) => setField(draftId, 'sku', e.target.value)} />
                  </label>
                  <label>申报价格
                    <input value={edit.core_fields?.declared_price ?? item.core_fields.declared_price ?? ''} onChange={(e) => setField(draftId, 'declared_price', e.target.value)} />
                  </label>
                  <label>建议售价
                    <input value={edit.core_fields?.suggested_price ?? item.core_fields.suggested_price ?? ''} onChange={(e) => setField(draftId, 'suggested_price', e.target.value)} />
                  </label>
                  <label>库存
                    <input value={edit.core_fields?.stock ?? item.core_fields.stock ?? ''} onChange={(e) => setField(draftId, 'stock', e.target.value)} />
                  </label>
                  <label>类目路径
                    <input value={edit.core_fields?.category_path ?? item.core_fields.category_path ?? ''} onChange={(e) => setField(draftId, 'category_path', e.target.value)} />
                  </label>
                  <label>类目ID
                    <input value={edit.core_fields?.category_id ?? item.core_fields.category_id ?? ''} onChange={(e) => setField(draftId, 'category_id', e.target.value)} />
                  </label>
                  <label>物流包裹长(cm)
                    <input value={edit.core_fields?.length_cm ?? item.core_fields.length_cm ?? ''} onChange={(e) => setField(draftId, 'length_cm', e.target.value)} />
                  </label>
                  <label>物流包裹宽(cm)
                    <input value={edit.core_fields?.width_cm ?? item.core_fields.width_cm ?? ''} onChange={(e) => setField(draftId, 'width_cm', e.target.value)} />
                  </label>
                  <label>物流包裹高(cm)
                    <input value={edit.core_fields?.height_cm ?? item.core_fields.height_cm ?? ''} onChange={(e) => setField(draftId, 'height_cm', e.target.value)} />
                  </label>
                  <label>重量(g)
                    <input value={edit.core_fields?.weight_g ?? item.core_fields.weight_g ?? ''} onChange={(e) => setField(draftId, 'weight_g', e.target.value)} />
                  </label>
                </div>
              </div>

              {/* 右列：原图 / 主图 / 轮播图 / 详情图 */}
              <div className="precheck-images">
                <div className="precheck-dimension-entry">
                  <div><strong>商品本体尺寸图</strong><span>独立画布制作，不使用上方物流包裹尺寸</span></div>
                  <button className="btn-mini primary" onClick={() => onOpenDimensionItem(taskId, item.item_id)}>添加尺寸图</button>
                </div>
                <div className="precheck-image-block">
                  <span className="precheck-block-title">来源原图（可点击选为替换素材）</span>
                  <div className="precheck-thumbs">
                    {item.source_image_urls.length === 0 && <span className="verify-empty">无来源图</span>}
                    {item.source_image_urls.map((url, index) => (
                      <img key={`${url}-${index}`} className="precheck-thumb" src={url} alt={`原图${index + 1}`} onClick={() => setActiveImage(url)} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />
                    ))}
                  </div>
                </div>

                <div className="precheck-image-block">
                  <span className="precheck-block-title">主图（导出预览图 / *产品素材图）</span>
                  <div className="precheck-thumbs">
                    {view.main_image ? (
                      <img className="precheck-thumb is-main" src={view.main_image} alt="主图" onClick={() => setActiveImage(view.main_image)} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />
                    ) : <span className="verify-empty">无主图</span>}
                    <button className="btn-mini" onClick={() => openFilePicker(draftId, 'main')}>上传替换</button>
                  </div>
                </div>

                <div className="precheck-image-block">
                  <span className="precheck-block-title">轮播图（导出 *轮播图，总览图在最后）</span>
                  <div className="precheck-thumbs">
                    {view.carousel.length === 0 && <span className="verify-empty">无轮播图</span>}
                    {view.carousel.map((url, index) => (
                      <div key={`${url}-${index}`} className="precheck-thumb-wrap">
                        <img className="precheck-thumb" src={url} alt={`轮播${index + 1}`} onClick={() => setActiveImage(url)} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />
                        <div className="precheck-thumb-actions">
                          <button className="btn-mini" title="设为主图" onClick={() => setCarouselAsMain(draftId, url)}>设主图</button>
                          <button className="btn-mini danger" title="删除" onClick={() => removeImage(draftId, 'carousel', index)}>删除</button>
                        </div>
                      </div>
                    ))}
                    <button className="btn-mini" onClick={() => openFilePicker(draftId, 'carousel')}>上传追加</button>
                  </div>
                </div>

                <div className="precheck-image-block">
                  <span className="precheck-block-title">详情图（导出时以 HTML 追加到产品描述）</span>
                  <div className="precheck-thumbs">
                    {view.detail.length === 0 && <span className="verify-empty">无详情图</span>}
                    {view.detail.map((url, index) => (
                      <div key={`${url}-${index}`} className="precheck-thumb-wrap">
                        <img className="precheck-thumb" src={url} alt={`详情${index + 1}`} onClick={() => setActiveImage(url)} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />
                        <div className="precheck-thumb-actions">
                          <button className="btn-mini danger" title="删除" onClick={() => removeImage(draftId, 'detail', index)}>删除</button>
                        </div>
                      </div>
                    ))}
                    <button className="btn-mini" onClick={() => openFilePicker(draftId, 'detail')}>上传追加</button>
                  </div>
                </div>
              </div>
            </div>
          </section>
        );
      })}

      {activeImage && (
        <div className="precheck-lightbox" onClick={() => setActiveImage(null)}>
          <img src={activeImage} alt="预览大图" />
          <button className="precheck-lightbox-close" aria-label="关闭">×</button>
        </div>
      )}
    </div>
  );
}

export default ProductProcessingPrecheckPage;
