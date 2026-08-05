import { useEffect, useState } from "react";

import {
  listProductDrafts,
  retryProductDraftSourceImages,
  type DraftSourceType,
  type ProductDraft,
} from "../api/productProcessingApi";

const VIEWS = [
  { key: "all", label: "全部草稿", sourceType: undefined },
  { key: "manual", label: "网页手动采集", sourceType: "web_manual_capture" },
  { key: "api", label: "万邦 API 采集", sourceType: "onebound_api" },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

const sourceLabels: Record<DraftSourceType, string> = {
  web_manual_capture: "网页手动采集",
  onebound_api: "万邦 API 采集",
};

function imageSource(draft: ProductDraft, version: number) {
  if (draft.image_path) return `/product-processing/drafts/${draft.id}/image?v=${version}`;
  return draft.image_url;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

function isExternalLink(value: string) {
  return /^https?:\/\//i.test(value);
}

export function ProductProcessingPage() {
  const [activeView, setActiveView] = useState<ViewKey>("all");
  const [drafts, setDrafts] = useState<ProductDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("正在读取产品草稿…");
  const [failedImages, setFailedImages] = useState<Record<number, boolean>>({});
  const [retryingId, setRetryingId] = useState<number>();
  const [imageVersion, setImageVersion] = useState(0);

  const selectedView = VIEWS.find((view) => view.key === activeView)!;
  const loadDrafts = async (sourceType = selectedView.sourceType) => {
    setLoading(true);
    try {
      const nextDrafts = await listProductDrafts(sourceType);
      setDrafts(nextDrafts);
      setFailedImages({});
      setNotice(nextDrafts.length ? `已显示 ${nextDrafts.length} 条草稿。` : "当前视图暂无产品草稿。");
    } catch (error) {
      setDrafts([]);
      setNotice(`读取产品草稿失败：${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadDrafts(selectedView.sourceType); }, [activeView]);

  const retryImage = async (draft: ProductDraft) => {
    setRetryingId(draft.id);
    try {
      await retryProductDraftSourceImages(draft.id);
      setFailedImages((items) => ({ ...items, [draft.id]: false }));
      setImageVersion((value) => value + 1);
      setNotice(`已提交“${draft.title || `草稿 #${draft.id}`}”的图片补齐请求。`);
    } catch (error) {
      setNotice(`图片补齐请求失败：${errorMessage(error)}`);
    } finally {
      setRetryingId(undefined);
    }
  };

  return <div className="product-processing-page">
    <section className="product-processing-hero">
      <div>
        <p className="eyebrow">PRODUCT PROCESSING · DRAFT INTAKE</p>
        <h1>产品处理</h1>
        <p>按采集来源查看待处理商品，并及时补齐未同步的来源图片。</p>
      </div>
      <button className="product-processing-refresh" type="button" onClick={() => void loadDrafts()} disabled={loading}>↻ 刷新草稿</button>
    </section>

    <section className="product-processing-surface">
      <div className="product-processing-tabs" role="tablist" aria-label="产品草稿来源">
        {VIEWS.map((view) => <button key={view.key} type="button" role="tab" aria-selected={activeView === view.key} className={activeView === view.key ? "is-active" : ""} onClick={() => setActiveView(view.key)}>{view.label}</button>)}
      </div>
      <p className="product-processing-notice" role="status" aria-live="polite">{notice}</p>

      {loading ? <div className="product-processing-empty"><span>◌</span><strong>正在加载草稿</strong><p>请稍候，正在按来源整理商品信息。</p></div> : drafts.length ? <div className="product-draft-grid">
        {drafts.map((draft) => {
          const source = imageSource(draft, imageVersion);
          const imageFailed = failedImages[draft.id] || !source;
          const platform = draft.raw_payload.source_platform || "未标注平台";
          const mode = draft.raw_payload.collection_mode;
          return <article className="product-draft-card" key={draft.id}>
            <div className="product-draft-image">
              {!imageFailed && <img src={source} alt={draft.title || `产品草稿 #${draft.id}`} onError={() => setFailedImages((items) => ({ ...items, [draft.id]: true }))} />}
              {imageFailed && <div className="product-draft-image-missing"><span>▧</span><strong>图片待补齐</strong><button type="button" onClick={() => void retryImage(draft)} disabled={retryingId === draft.id}>{retryingId === draft.id ? "正在提交…" : "重新同步图片"}</button></div>}
              <span className="product-draft-source">{sourceLabels[draft.source_type]}</span>
            </div>
            <div className="product-draft-body">
              <h2>{draft.title || `未命名草稿 #${draft.id}`}</h2>
              <dl>
                <div><dt>来源平台</dt><dd>{platform}</dd></div>
                {mode && <div><dt>API 模式</dt><dd>{mode}</dd></div>}
                <div><dt>来源链接</dt><dd>{isExternalLink(draft.source_ref) ? <a href={draft.source_ref} target="_blank" rel="noreferrer">打开商品页面 ↗</a> : (draft.source_ref || "未提供")}</dd></div>
              </dl>
            </div>
          </article>;
        })}
      </div> : <div className="product-processing-empty"><span>▣</span><strong>暂无草稿</strong><p>可从网页手动采集或万邦 API 采集商品，采集后的草稿会显示在这里。</p></div>}
    </section>
  </div>;
}
