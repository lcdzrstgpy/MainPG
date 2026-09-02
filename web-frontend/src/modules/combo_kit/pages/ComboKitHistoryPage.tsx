import { useCallback, useEffect, useMemo, useState } from 'react';
import { getAuthAccount, getAuthToken } from '../../../transport/http/client';
import type { ApiContext } from '../../product_processing/api/client';
import { deleteSet, listSets, type ComboKitSet } from '../../product_processing/api/comboKitApi';
import '../styles/comboKit.css';

type Props = { isActive?: boolean; onOpenSet: (setId: string) => void };
const PAGE_SIZE = 10;

function api(): ApiContext {
  const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
  return { baseUrl: '', token: getAuthToken(), workspaceId: account.workspace_id || account.workspace_code || 'default' };
}

function displayTime(value?: string): string {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

export function ComboKitHistoryPage({ isActive = true, onOpenSet }: Props) {
  const ctx = useMemo(() => api(), []);
  const [sets, setSets] = useState<ComboKitSet[]>([]);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await listSets(ctx);
      setSets(data.sets || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [ctx]);

  useEffect(() => { if (isActive) void load(); }, [isActive, load]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    if (!keyword) return sets;
    return sets.filter((item) => [item.name, item.sku, item.sku_display, item.category_name, item.category_path].some((value) => String(value || '').toLocaleLowerCase().includes(keyword)));
  }, [query, sets]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => { setPage(1); }, [query]);
  useEffect(() => { if (page > totalPages) setPage(totalPages); }, [page, totalPages]);

  const remove = async (item: ComboKitSet) => {
    if (!window.confirm(`确定删除组合套装「${item.name || item.sku_display || item.set_id}」吗？`)) return;
    try {
      await deleteSet(ctx, item.set_id);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <div><h1>历史组合套装</h1><p>查看已保存的组合套装，搜索后可回到组合生图继续制作。</p></div>
        <div className="combo-header-actions"><button type="button" onClick={() => void load()} disabled={loading}>刷新</button></div>
      </header>
      {error && <div className="combo-kit-message error">{error}</div>}
      <div className="combo-history-search"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索套装名称 / SKU / 类目" /></div>
      <section className="combo-history-table">
        <div className="combo-history-row combo-history-header"><span>套装名称</span><span>SKU</span><span>状态</span><span>更新时间</span><span>操作</span></div>
        {loading && <div className="combo-history-empty">正在读取历史套装…</div>}
        {!loading && visible.length === 0 && <div className="combo-history-empty">暂无符合条件的组合套装。</div>}
        {!loading && visible.map((item) => (
          <div className="combo-history-row" key={item.set_id}>
            <span className="combo-history-name">{item.name || '未命名套装'}</span>
            <span>{item.sku_display || item.sku || '-'}</span>
            <span>{item.status || item.stage || '-'}</span>
            <span>{displayTime(item.updated_at || item.created_at)}</span>
            <span className="combo-history-actions">
              <button type="button" className="btn-mini primary" onClick={() => onOpenSet(item.set_id)}>继续制作</button>
              <button type="button" className="btn-mini danger" onClick={() => void remove(item)}>删除</button>
            </span>
          </div>
        ))}
      </section>
      {totalPages > 1 && <div className="combo-history-pager"><button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1}>上一页</button><span>第 {page} / {totalPages} 页，共 {filtered.length} 条</span><button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page >= totalPages}>下一页</button></div>}
    </div>
  );
}

export default ComboKitHistoryPage;
