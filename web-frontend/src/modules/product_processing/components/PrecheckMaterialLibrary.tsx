import {
  addAssets,
  assetFullUrl,
  assetThumbUrl,
  removeFromLibrary,
  selectMainAsset,
} from "../data/precheckImageModel";
import type { PreviewImageAsset, PreviewImageManifest } from "../types";

type PrecheckMaterialLibraryProps = {
  assets: PreviewImageAsset[];
  manifest: PreviewImageManifest;
  disabled: boolean;
  onManifestChange: (manifest: PreviewImageManifest) => void;
  onRemoveFromLibrary: (assetId: string) => void;
  onPreview: (url: string) => void;
  onCompose?: () => void;
};

const CATEGORY_ORDER = ["generated", "dimension", "upload", "source"] as const;

const CATEGORY_LABELS: Record<string, string> = {
  generated: "AI 处理图",
  dimension: "尺寸图",
  upload: "本地导入",
  source: "已选原图",
};

function assetUrl(asset: PreviewImageAsset): string {
  return assetThumbUrl(asset);
}

function categoryOf(asset: PreviewImageAsset): string {
  if (asset.bucket === "source") return "source";
  if (asset.origin === "generated") return "generated";
  if (asset.origin === "dimension") return "dimension";
  if (asset.origin === "upload") return "upload";
  return asset.origin;
}

function MaterialCard({
  asset,
  category,
  manifest,
  disabled,
  onManifestChange,
  onRemoveFromLibrary,
  onPreview,
}: {
  asset: PreviewImageAsset;
  category: string;
  manifest: PreviewImageManifest;
  disabled: boolean;
  onManifestChange: (manifest: PreviewImageManifest) => void;
  onRemoveFromLibrary: (assetId: string) => void;
  onPreview: (url: string) => void;
}) {
  const url = assetUrl(asset);
  const fullUrl = assetFullUrl(asset);
  const isMain = manifest.main_asset_id === asset.id;
  const inCarousel = manifest.carousel_asset_ids.includes(asset.id);
  const inDetail = manifest.detail_asset_ids.includes(asset.id);
  const isSource = category === "source";
  const inExport = isMain || inCarousel || inDetail;
  const primaryAction = !inCarousel ? "carousel" : !isMain ? "main" : !inDetail ? "detail" : "";
  return (
    <article className="precheck-asset-card material-library-card">
      <button
        type="button"
        className="precheck-asset-preview"
        disabled={!fullUrl}
        aria-label={`预览${CATEGORY_LABELS[category] ?? category}`}
        onClick={() => fullUrl && onPreview(fullUrl)}
      >
        {url ? (
          <img
            src={url}
            alt={CATEGORY_LABELS[category] ?? category}
            loading="lazy"
            onError={(event) => { event.currentTarget.style.visibility = "hidden"; }}
          />
        ) : (
          <span>无预览</span>
        )}
      </button>
      <div className="precheck-asset-meta">
        <span className={`precheck-origin-badge origin-${asset.origin}`}>
          {CATEGORY_LABELS[category] ?? category}
        </span>
      </div>
      <div className="precheck-asset-status">
        {isMain && <span className="is-main">主图</span>}
        {inCarousel && <span className="is-carousel">轮播</span>}
        {inDetail && <span className="is-detail">详情</span>}
        {isSource && <span className="is-source">原图</span>}
      </div>
      <div className="precheck-card-actions">
        <button
          type="button"
          className={primaryAction === "carousel" ? "primary" : "ghost"}
          disabled={disabled || inCarousel}
          onClick={() => onManifestChange(addAssets(manifest, "carousel", [asset.id]))}
        >{inCarousel ? "已在轮播" : "加入轮播"}</button>
        <button
          type="button"
          className={primaryAction === "main" ? "primary" : "ghost"}
          disabled={disabled || isMain}
          onClick={() => onManifestChange(selectMainAsset(manifest, asset.id))}
        >{isMain ? "当前主图" : "设为主图"}</button>
        <button
          type="button"
          className={primaryAction === "detail" ? "primary" : "ghost"}
          disabled={disabled || inDetail}
          onClick={() => onManifestChange(addAssets(manifest, "detail", [asset.id]))}
        >{inDetail ? "已在详情" : "加入详情"}</button>
        {isSource && (
          <button
            type="button"
            className="danger"
            disabled={disabled || inExport}
            title={inExport ? "请先从导出清单移除" : "移出素材库"}
            onClick={() => onRemoveFromLibrary(asset.id)}
          >移出素材库</button>
        )}
      </div>
    </article>
  );
}

export function PrecheckMaterialLibrary({
  assets,
  manifest,
  disabled,
  onManifestChange,
  onRemoveFromLibrary,
  onPreview,
  onCompose,
}: PrecheckMaterialLibraryProps) {
  const byCategory = new Map<string, PreviewImageAsset[]>();
  for (const category of CATEGORY_ORDER) byCategory.set(category, []);
  for (const asset of assets) {
    const category = categoryOf(asset);
    if (!byCategory.has(category)) byCategory.set(category, []);
    byCategory.get(category)?.push(asset);
  }

  const groups = CATEGORY_ORDER.filter((category) => (byCategory.get(category)?.length ?? 0) > 0);

  return (
    <section className="precheck-manager-section precheck-material-library">
      <header>
        <div>
          <h3>处理后素材库</h3>
          <p>AI 处理图、尺寸图与本地导入自动进入素材库；手动加入的原图也在此显示。</p>
        </div>
        {onCompose && assets.length > 0 && (
          <button
            type="button"
            className="btn-mini primary"
            disabled={disabled}
            onClick={onCompose}
          >
            本地拼装
          </button>
        )}
      </header>
      {assets.length === 0 ? (
        <div className="precheck-manager-empty">暂无处理后素材，可从处理前图片池加入原图。</div>
      ) : groups.map((category) => {
        const group = byCategory.get(category) ?? [];
        return (
          <section key={category} className="precheck-material-group">
            <header>
              <strong>{CATEGORY_LABELS[category] ?? category}</strong>
              <span>{group.length} 项</span>
            </header>
            <div className="precheck-asset-grid">
              {group.map((asset) => (
                <MaterialCard
                  key={asset.id}
                  asset={asset}
                  category={category}
                  manifest={manifest}
                  disabled={disabled}
                  onManifestChange={onManifestChange}
                  onRemoveFromLibrary={onRemoveFromLibrary}
                  onPreview={onPreview}
                />
              ))}
            </div>
          </section>
        );
      })}
    </section>
  );
}

export default PrecheckMaterialLibrary;
