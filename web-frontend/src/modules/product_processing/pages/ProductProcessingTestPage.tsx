import { useEffect, useMemo, useState } from 'react';
import {
  ppDownload,
  ppRequest,
  ppUpload,
  type ApiContext,
} from '../api/client';
import {
  type DraftSummary,
  type DraftUpdateRequest,
  type DraftVariant,
  type ProductProcessingOptions,
  type ProcessingScopeOption,
  type TaskHistoryItem,
  type TaskHistoryResponse,
  type TaskOutputsResponse,
} from '../types';
import '../styles/ProductProcessingTestPage.css';

const API_BASE = '/api/product-processing';

const SITES: { code: 'US' | 'CO' | 'EC'; label: string }[] = [
  { code: 'US', label: '美国站' },
  { code: 'CO', label: '哥伦比亚站' },
  { code: 'EC', label: '厄瓜多尔站' },
];

const LANGUAGES: { code: 'en' | 'es'; label: string }[] = [
  { code: 'en', label: '英语 · English' },
  { code: 'es', label: '西班牙语 · Español' },
];

const PROCESSING_SCOPE_OPTIONS: ProcessingScopeOption[] = [
  { key: 'title', label: '标题' },
  { key: 'details', label: '详情' },
  { key: 'product_dimensions', label: '产品尺寸' },
  { key: 'four_grid', label: '四宫格' },
  { key: 'detail_images', label: '详情图' },
  { key: 'sku_images', label: 'SKU 图' },
  { key: 'qualification', label: '资质' },
];

const DEFAULT_OPTIONS: ProductProcessingOptions = {
  targetSite: 'US',
  targetLanguage: 'en',
  maxProducts: 0,
  processingScope: ['title', 'details', 'product_dimensions', 'four_grid', 'detail_images', 'qualification'],
  qualificationMode: 'standard',
  includeProductVideo: false,
  skipDuplicates: false,
  ipCheck: true,
};

function getApiContext(): ApiContext {
  return {
    baseUrl: '',
    token: '',
    workspaceId: 'default',
  };
}

function formatDateTime(value: string): string {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString('zh-CN');
  } catch {
    return value;
  }
}

function imageUrlStatus(url: string): 'ok' | 'missing' | 'unsafe' {
  if (!url) return 'missing';
  const lower = url.toLowerCase();
  if (lower.startsWith('http://') || lower.startsWith('https://')) return 'ok';
  if (lower.startsWith('data:')) return 'ok';
  return 'unsafe';
}

export function ProductProcessingTestPage() {
  const api = getApiContext();

  const [options, setOptions] = useState<ProductProcessingOptions>(DEFAULT_OPTIONS);
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editTitleMap, setEditTitleMap] = useState<Record<number, string>>({});
  const [editImageUrlMap, setEditImageUrlMap] = useState<Record<number, string>>({});
  const [editSkuNameMap, setEditSkuNameMap] = useState<Record<string, string>>({});
  const [editSkuDeletes, setEditSkuDeletes] = useState<Set<string>>(new Set());

  const [tasks, setTasks] = useState<TaskHistoryItem[]>([]);
  const [currentTask, setCurrentTask] = useState<TaskOutputsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [importFile, setImportFile] = useState<File | null>(null);
  const [manualTitle, setManualTitle] = useState('');
  const [manualImageUrl, setManualImageUrl] = useState('');
  const [manualCategory, setManualCategory] = useState('');

  const selectedDrafts = useMemo(
    () => drafts.filter((d) => selectedIds.has(d.id)),
    [drafts, selectedIds]
  );

  const fetchDrafts = async () => {
    try {
      const data = await ppRequest<{ drafts: DraftSummary[] }>(api, `${API_BASE}/drafts`);
      setDrafts(data.drafts || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '拉取草稿池失败');
    }
  };

  const fetchTasks = async () => {
    try {
      const data = await ppRequest<TaskHistoryResponse>(api, `${API_BASE}/tasks/history?limit=20`);
      setTasks(data.tasks || []);
    } catch (err) {
      // 历史任务失败不阻塞主流程
    }
  };

  useEffect(() => {
    fetchDrafts();
    fetchTasks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!currentTask) return;
    const runningStatuses = ['queued', 'running', 'paused'];
    if (!runningStatuses.includes(currentTask.task.status)) return;
    const timer = setInterval(async () => {
      try {
        const data = await ppRequest<TaskOutputsResponse>(
          api,
          `${API_BASE}/tasks/${currentTask.task_id}/outputs`
        );
        setCurrentTask(data);
        if (!runningStatuses.includes(data.task.status)) {
          clearInterval(timer);
          fetchTasks();
        }
      } catch {
        clearInterval(timer);
      }
    }, 2000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTask?.task_id, currentTask?.task.status]);

  const handleSiteChange = (site: 'US' | 'CO' | 'EC') => {
    setOptions((prev) => {
      const defaultLang = SITES.find((s) => s.code === site)?.code === 'CO' || site === 'EC' ? 'es' : 'en';
      return { ...prev, targetSite: site, targetLanguage: defaultLang };
    });
  };

  const toggleScope = (key: string) => {
    setOptions((prev) => {
      const next = new Set(prev.processingScope);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return { ...prev, processingScope: Array.from(next) };
    });
  };

  const toggleDraft = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 100) next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    const selectable = drafts.filter((d) => d.status !== 'deleted').slice(0, 100).map((d) => d.id);
    setSelectedIds(new Set(selectable));
  };

  const clearSelection = () => setSelectedIds(new Set());

  const deleteSelected = async () => {
    if (!selectedIds.size) return;
    setLoading(true);
    try {
      await ppRequest(api, `${API_BASE}/drafts/delete`, {
        body: { draft_ids: Array.from(selectedIds), delete_all: false },
      });
      setSelectedIds(new Set());
      await fetchDrafts();
      setMessage(`已删除 ${selectedIds.size} 条草稿`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setLoading(false);
    }
  };

  const saveDraftEdits = async (draft: DraftSummary) => {
    const payload: DraftUpdateRequest = {};
    if (editTitleMap[draft.id] !== undefined) payload.title = editTitleMap[draft.id];
    if (editImageUrlMap[draft.id] !== undefined) payload.image_url = editImageUrlMap[draft.id];

    const variants: DraftVariant[] = draft.raw_payload?.source_variant_records || [];
    if (variants.length > 0) {
      const edits: Record<string, string> = {};
      const deletes: string[] = [];
      variants.forEach((variant) => {
        const attributes = variant.attributes || {};
        const label = Object.values(attributes).join('/');
        const skuId = String(variant.sku_id || variant.source_sku_id || label);
        if (editSkuDeletes.has(skuId) || editSkuDeletes.has(label)) {
          deletes.push(label);
          return;
        }
        const displayName = editSkuNameMap[skuId] ?? editSkuNameMap[label];
        if (displayName !== undefined && displayName !== (variant.display_name || label)) {
          edits[label] = displayName;
        }
      });
      if (Object.keys(edits).length) payload.sku_name_edits = edits;
      if (deletes.length) payload.sku_name_deletes = deletes;
    }

    try {
      await ppRequest(api, `${API_BASE}/drafts/${draft.id}`, {
        method: 'PATCH',
        body: payload,
      });
      await fetchDrafts();
      setMessage('草稿已保存');
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    }
  };

  const createManualDraft = async () => {
    if (!manualTitle.trim()) {
      setError('请输入标题');
      return;
    }
    setLoading(true);
    try {
      await ppRequest(api, `${API_BASE}/drafts`, {
        body: {
          source_type: 'manual',
          title: manualTitle,
          product_name: manualTitle,
          category: manualCategory,
          image_url: manualImageUrl,
        },
      });
      setManualTitle('');
      setManualImageUrl('');
      setManualCategory('');
      await fetchDrafts();
      setMessage('已创建手动草稿');
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  const importWorkbook = async () => {
    if (!importFile) {
      setError('请选择 Excel 文件');
      return;
    }
    setLoading(true);
    const formData = new FormData();
    formData.append('file', importFile);
    formData.append('title', '产品处理任务-Excel 导入');
    formData.append('target_site', options.targetSite);
    formData.append('target_language', options.targetLanguage);
    formData.append('processing_scope', options.processingScope.join(','));
    formData.append('qualification_mode', options.qualificationMode);
    formData.append('include_product_video', String(options.includeProductVideo));
    try {
      const data = await ppUpload<TaskOutputsResponse>(api, `${API_BASE}/import`, formData);
      setCurrentTask(data);
      setMessage(data.message || '导入并处理完成');
      await fetchDrafts();
      await fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入失败');
    } finally {
      setLoading(false);
    }
  };

  const runProcess = async (preflightOnly = false) => {
    if (!selectedIds.size) {
      setError('请先选择要处理的草稿');
      return;
    }
    setLoading(true);
    setError('');
    setMessage('');
    try {
      const body = {
        title: preflightOnly ? '草稿池预检' : '产品处理任务-草稿池商品',
        draft_ids: Array.from(selectedIds),
        max_products: options.maxProducts,
        async_mode: true,
        preflight_only: preflightOnly,
        target_site: options.targetSite,
        target_language: options.targetLanguage,
        processing_scope: options.processingScope,
        qualification_mode: options.qualificationMode,
        include_product_video: options.includeProductVideo,
        skip_duplicates: options.skipDuplicates,
        ip_check: options.ipCheck,
      };
      const data = await ppRequest<TaskOutputsResponse>(api, `${API_BASE}/drafts/process`, {
        body,
      });
      setCurrentTask(data);
      setMessage(data.message || (preflightOnly ? '预检完成' : '处理任务已提交'));
      await fetchDrafts();
      await fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : '处理失败');
    } finally {
      setLoading(false);
    }
  };

  const pauseTask = async () => {
    if (!currentTask) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(
        api,
        `${API_BASE}/tasks/${currentTask.task_id}/pause`,
        { body: {} }
      );
      setCurrentTask(data);
      setMessage(data.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : '暂停失败');
    }
  };

  const resumeTask = async () => {
    if (!currentTask) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(
        api,
        `${API_BASE}/tasks/${currentTask.task_id}/resume`,
        { body: {} }
      );
      setCurrentTask(data);
      setMessage(data.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : '继续失败');
    }
  };

  const retryFailed = async () => {
    if (!currentTask) return;
    try {
      const data = await ppRequest<TaskOutputsResponse>(
        api,
        `${API_BASE}/tasks/${currentTask.task_id}/retry-attention`,
        { body: {} }
      );
      setCurrentTask(data);
      setMessage(data.message);
      await fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : '重试失败');
    }
  };

  const clearTask = async () => {
    if (!currentTask) return;
    try {
      await ppRequest(api, `${API_BASE}/tasks/${currentTask.task_id}/clear`, { body: {} });
      setCurrentTask(null);
      setMessage('已清空当前任务');
      await fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : '清空失败');
    }
  };

  const loadTaskOutputs = async (taskId: number) => {
    try {
      const data = await ppRequest<TaskOutputsResponse>(api, `${API_BASE}/tasks/${taskId}/outputs`);
      setCurrentTask(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载任务详情失败');
    }
  };

  const downloadArtifact = (kind: 'dxm' | 'errors' | 'video_manifest', filename: string) => {
    if (!currentTask) return;
    ppDownload(api, `${API_BASE}/tasks/${currentTask.task_id}/download?kind=${kind}`, filename);
  };

  const failureItems = useMemo(
    () => currentTask?.items.filter((item) => item.status === 'attention_required' || item.status === 'failed') || [],
    [currentTask]
  );

  return (
    <div className="product-processing-test-page">
      <h1>产品处理</h1>

      {(message || error) && (
        <div className={`message ${error ? 'error' : ''}`}>
          {error || message}
        </div>
      )}

      <section className="section import-section">
        <h2>导入 / 创建草稿</h2>
        <div className="form-row">
          <input
            type="file"
            accept=".xlsx,.xls,.csv"
            onChange={(e) => setImportFile(e.target.files?.[0] || null)}
          />
          <button onClick={importWorkbook} disabled={loading || !importFile}>
            导入 Excel 并处理
          </button>
        </div>
        <div className="form-row manual-draft">
          <input
            type="text"
            placeholder="标题"
            value={manualTitle}
            onChange={(e) => setManualTitle(e.target.value)}
          />
          <input
            type="text"
            placeholder="主图 URL"
            value={manualImageUrl}
            onChange={(e) => setManualImageUrl(e.target.value)}
          />
          <input
            type="text"
            placeholder="类目"
            value={manualCategory}
            onChange={(e) => setManualCategory(e.target.value)}
          />
          <button onClick={createManualDraft} disabled={loading}>
            创建手动草稿
          </button>
        </div>
      </section>

      <section className="section settings-section">
        <h2>处理设置</h2>
        <div className="form-row">
          <label>
            站点
            <select value={options.targetSite} onChange={(e) => handleSiteChange(e.target.value as any)}>
              {SITES.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            语言
            <select value={options.targetLanguage} onChange={(e) => setOptions((p) => ({ ...p, targetLanguage: e.target.value as any }))}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            处理数量（滑动选择，0=全部，最多 100）
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={options.maxProducts}
              onChange={(e) => setOptions((p) => ({ ...p, maxProducts: Number(e.target.value) || 0 }))}
              style={{ accentColor: '#0a8fca' }}
            />
            <span className="slider-value">
              {options.maxProducts === 0 ? '全部（≤100）' : options.maxProducts}
            </span>
          </label>
          <label>
            资质模式
            <select
              value={options.qualificationMode}
              onChange={(e) => setOptions((p) => ({ ...p, qualificationMode: e.target.value as any }))}
            >
              <option value="standard">标准</option>
              <option value="strict">严格</option>
            </select>
          </label>
        </div>

        <div className="form-row scope-row">
          <span className="scope-label">处理范围：</span>
          {PROCESSING_SCOPE_OPTIONS.map((opt) => (
            <label key={opt.key} className="scope-checkbox">
              <input
                type="checkbox"
                checked={options.processingScope.includes(opt.key)}
                onChange={() => toggleScope(opt.key)}
              />
              {opt.label}
            </label>
          ))}
        </div>

        <div className="form-row">
          <label className="scope-checkbox">
            <input
              type="checkbox"
              checked={options.includeProductVideo}
              onChange={(e) => setOptions((p) => ({ ...p, includeProductVideo: e.target.checked }))}
            />
            生成商品视频
          </label>
          <label className="scope-checkbox">
            <input
              type="checkbox"
              checked={options.skipDuplicates}
              onChange={(e) => setOptions((p) => ({ ...p, skipDuplicates: e.target.checked }))}
            />
            跳过已处理
          </label>
          <label className="scope-checkbox">
            <input
              type="checkbox"
              checked={options.ipCheck}
              onChange={(e) => setOptions((p) => ({ ...p, ipCheck: e.target.checked }))}
            />
            侵权词过滤
          </label>
        </div>
      </section>

      <section className="section draft-section">
        <div className="section-header">
          <h2>草稿池</h2>
          <div className="draft-actions">
            <span className="count-badge">
              待处理 {drafts.filter((d) => d.status !== 'deleted').length} / 已选 {selectedIds.size}
            </span>
            <button onClick={selectAll}>全选（最多 100）</button>
            <button onClick={clearSelection}>取消选择</button>
            <button onClick={deleteSelected} disabled={!selectedIds.size}>
              删除已选
            </button>
            <button onClick={fetchDrafts}>刷新</button>
          </div>
        </div>

        <div className="draft-list">
          {drafts.length === 0 && <p className="empty">草稿池为空</p>}
          {drafts.map((draft) => {
            const raw = draft.raw_payload || {};
            const isExpanded = expandedId === draft.id;
            const platform = raw.source_platform || raw.platform || 'manual';
            const variants: DraftVariant[] = raw.source_variant_records || [];
            const skuCount = variants.length || 1;
            const imgStatus = imageUrlStatus(draft.image_url || raw.main_image_url || '');
            const isSelected = selectedIds.has(draft.id);
            return (
              <div key={draft.id} className={`draft-card ${isSelected ? 'selected' : ''} ${draft.status === 'deleted' ? 'deleted' : ''}`}>
                <div className="draft-card-main">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleDraft(draft.id)}
                    disabled={draft.status === 'deleted'}
                  />
                  <div className="thumb">
                    {(draft.image_url || raw.main_image_url) ? (
                      <img src={draft.image_url || raw.main_image_url} alt="" referrerPolicy="no-referrer" />
                    ) : (
                      <span>无图</span>
                    )}
                  </div>
                  <div className="draft-info">
                    <div className="title-line">
                      <strong>{draft.title || raw.source_title || '(无标题)'}</strong>
                      <span className="badge">{platform}</span>
                      <span className="badge">SKU {skuCount}</span>
                      <span className="badge">rev 1</span>
                      <span className={`badge status-${imgStatus}`}>
                        主图 {imgStatus === 'ok' ? '可用' : imgStatus === 'missing' ? '缺失' : '不安全'}
                      </span>
                      {draft.status === 'processed' && <span className="badge processed">已处理</span>}
                    </div>
                    <div className="meta-line">
                      SKC: {draft.skc || '-'} | SKU: {draft.sku || '-'} | 来源: {draft.source_ref || '-'}
                    </div>
                  </div>
                  <div className="draft-actions">
                    <button onClick={() => setExpandedId(isExpanded ? null : draft.id)}>
                      {isExpanded ? '收起' : '编辑'}
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="draft-card-edit">
                    <div className="form-row">
                      <label>
                        标题
                        <input
                          type="text"
                          defaultValue={draft.title || raw.source_title || ''}
                          onChange={(e) => setEditTitleMap((m) => ({ ...m, [draft.id]: e.target.value }))}
                        />
                      </label>
                      <label>
                        主图 URL
                        <input
                          type="text"
                          defaultValue={draft.image_url || raw.main_image_url || ''}
                          onChange={(e) => setEditImageUrlMap((m) => ({ ...m, [draft.id]: e.target.value }))}
                        />
                      </label>
                    </div>
                    {variants.length > 0 && (
                      <div className="variant-list">
                        <h4>SKU 变体</h4>
                        {variants.map((variant, idx) => {
                          const attributes = variant.attributes || {};
                          const label = Object.values(attributes).join('/');
                          const skuId = String(variant.sku_id || variant.source_sku_id || label || idx);
                          const isDeleted = editSkuDeletes.has(skuId) || editSkuDeletes.has(label);
                          return (
                            <div key={idx} className={`variant-row ${isDeleted ? 'deleted' : ''}`}>
                              <span>{label || variant.display_name || `SKU ${idx + 1}`}</span>
                              <input
                                type="text"
                                placeholder="显示名称"
                                defaultValue={variant.display_name || label}
                                disabled={isDeleted}
                                onChange={(e) => setEditSkuNameMap((m) => ({ ...m, [skuId]: e.target.value }))}
                              />
                              <button
                                onClick={() =>
                                  setEditSkuDeletes((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(skuId)) next.delete(skuId);
                                    else next.add(skuId);
                                    return next;
                                  })
                                }
                              >
                                {isDeleted ? '恢复' : '删除'}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div className="form-row">
                      <button onClick={() => saveDraftEdits(draft)}>保存修改</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="section control-section">
        <h2>任务控制</h2>
        <div className="button-row">
          <button onClick={() => runProcess(true)} disabled={loading || !selectedIds.size}>
            预检
          </button>
          <button onClick={() => runProcess(false)} disabled={loading || !selectedIds.size}>
            开始处理
          </button>
          <button onClick={pauseTask} disabled={!currentTask}>
            暂停
          </button>
          <button onClick={resumeTask} disabled={!currentTask}>
            继续
          </button>
          <button onClick={retryFailed} disabled={!currentTask}>
            重试失败项
          </button>
          <button onClick={clearTask} disabled={!currentTask}>
            清空任务
          </button>
        </div>
      </section>

      {currentTask && (
        <section className="section result-section">
          <h2>处理结果</h2>
          <div className="result-summary">
            <div className="count-card">总数 <b>{currentTask.total_count}</b></div>
            <div className="count-card success">成功 <b>{currentTask.success_count}</b></div>
            <div className="count-card failed">失败 <b>{currentTask.failed_count}</b></div>
            <div className="count-card">跳过 <b>{currentTask.skipped_count}</b></div>
            <div className="count-card">待确认 <b>{currentTask.attention_required_count}</b></div>
            <div className="count-card">技术可重试 <b>{currentTask.technical_retryable_count}</b></div>
            <div className="count-card">配置阻断 <b>{currentTask.configuration_blocked_count}</b></div>
            <div className="count-card">身份待复核 <b>{currentTask.identity_review_required_count}</b></div>
            <div className="count-card">尺寸待复核 <b>{currentTask.logistics_review_required_count}</b></div>
          </div>
          <p className="task-message">{currentTask.message || currentTask.task.status}</p>

          <div className="download-row">
            {currentTask.outputs.dxm_import && (
              <button onClick={() => downloadArtifact('dxm', `dxm_import_task_${currentTask!.task_id}.xlsx`)}>
                下载店小秘导入表
              </button>
            )}
            {currentTask.outputs.error_report && (
              <button onClick={() => downloadArtifact('errors', `error_report_task_${currentTask!.task_id}.csv`)}>
                下载失败原因表
              </button>
            )}
            {currentTask.outputs.product_video_manifest && (
              <button onClick={() => downloadArtifact('video_manifest', `product_video_manifest_task_${currentTask!.task_id}.csv`)}>
                下载视频清单
              </button>
            )}
          </div>

          {failureItems.length > 0 && (
            <div className="failure-panel">
              <h3>失败/待确认商品</h3>
              <table className="failure-table">
                <thead>
                  <tr>
                    <th>SKC</th>
                    <th>标题</th>
                    <th>状态</th>
                    <th>失败类型</th>
                    <th>原因</th>
                    <th>操作提示</th>
                  </tr>
                </thead>
                <tbody>
                  {failureItems.map((item) => (
                    <tr key={item.id}>
                      <td>{item.skc || '-'}</td>
                      <td>{item.title || '-'}</td>
                      <td>{item.status}</td>
                      <td>{String((item.result as any)?.failure_class || '-')}</td>
                      <td>{item.reason || '-'}</td>
                      <td>{String((item.result as any)?.operator_hint || '-')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="section history-section">
        <h2>历史任务</h2>
        {tasks.length === 0 && <p className="empty">暂无历史任务</p>}
        <ul className="task-history">
          {tasks.map((task) => (
            <li key={task.task_id} className="history-item">
              <span className="history-title">{task.title}</span>
              <span className="history-status">{task.status}</span>
              <span className="history-counts">
                总数 {task.total_count} / 成功 {task.success_count} / 失败 {task.failed_count}
              </span>
              <span className="history-date">{formatDateTime(task.created_at)}</span>
              <button onClick={() => loadTaskOutputs(task.task_id)}>查看</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default ProductProcessingTestPage;
