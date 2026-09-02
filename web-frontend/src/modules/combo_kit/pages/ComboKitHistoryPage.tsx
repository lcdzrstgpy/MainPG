import { useEffect, useMemo, useState } from "react";

import { getAuthAccount, getAuthToken } from "../../../transport/http/client";
import type { ApiContext } from "../../product_processing/api/client";
import { listSets, type ComboKitSet } from "../../product_processing/api/comboKitApi";
import "../styles/comboKit.css";

type Props = {
  isActive?: boolean;
  onOpenSet: (setId: string) => void;
};

function api(): ApiContext {
  const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
  return {
    baseUrl: "",
    token: getAuthToken(),
    workspaceId: account.workspace_id || account.workspace_code || "default",
  };
}

function formatTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export function ComboKitHistoryPage({ isActive = true, onOpenSet }: Props) {
  const ctx = useMemo(() => api(), []);
  const [sets, setSets] = useState<ComboKitSet[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadSets = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listSets(ctx);
      setSets(result.sets ?? []);
    } catch (cause) {
      setSets([]);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isActive) void loadSets();
    // 页面再次激活时应刷新历史；请求上下文在挂载期间稳定。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  const visibleSets = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return sets;
    return sets.filter((set) => `${set.name} ${set.sku} ${set.sku_display}`.toLocaleLowerCase().includes(normalized));
  }, [query, sets]);

  return (
    <div className="combo-kit-page">
      <header className="combo-kit-header">
        <div>
          <h1>历史组合套装</h1>
          <p>查看已创建的组合套装，随时回到组合生图继续编辑、生成或导出。</p>
        </div>
        <div className="combo-header-actions">
          <button type="button" onClick={() => void loadSets()} disabled={loading || !isActive}>刷新</button>
        </div>
      </header>

      <div className="combo-history-search">
        <input
          aria-label="搜索历史组合套装"
          placeholder="按套装名称或 SKU 搜索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span>共 {visibleSets.length} 条</span>
      </div>

      {error && <div className="combo-kit-message error">{error}</div>}
      <section className="combo-history-table" aria-label="组合套装历史列表">
        <div className="combo-history-row combo-history-header" aria-hidden="true">
          <span>套装名称</span><span>SKU</span><span>状态</span><span>更新时间</span><span>操作</span>
        </div>
        {loading && <p className="combo-history-empty">正在加载历史组合套装…</p>}
        {!loading && visibleSets.length === 0 && <p className="combo-history-empty">暂无匹配的组合套装。</p>}
        {!loading && visibleSets.map((set) => (
          <div className="combo-history-row" key={set.set_id}>
            <strong className="combo-history-name">{set.name || "未命名套装"}</strong>
            <span>{set.sku_display || set.sku || "—"}</span>
            <span>{set.status || set.stage || "草稿"}</span>
            <time dateTime={set.updated_at}>{formatTime(set.updated_at ?? set.created_at)}</time>
            <div className="combo-history-actions">
              <button type="button" className="btn-mini primary" onClick={() => onOpenSet(set.set_id)}>继续制作</button>
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

export default ComboKitHistoryPage;
