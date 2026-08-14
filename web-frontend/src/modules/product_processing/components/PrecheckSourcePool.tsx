import { mediaStatusLabel, supportsMediaRetry } from "../data/draftMediaModel";
import type { PreviewImageAsset } from "../types";

const SOURCE_KIND_ORDER = ["main", "gallery", "sku", "detail"] as const;

const SOURCE_KIND_LABELS: Record<string, string> = {
  main: "原始主图",
  gallery: "原始轮播",
  sku: "原始 SKU",
  detail: "原始详情",
};

type PrecheckSourcePoolProps = {
  assets: PreviewImageAsset[];
  libraryAssetIds: ReadonlySet<string>;
  disabled: boolean;
  retryingMediaAssetIds?: ReadonlySet<string>;
  onPromote: (assetId: string) => void;
  onRetry?: (mediaAssetId: string) => void;
  onPreview: (url: string) => void;
};

function assetUrl(asset: PreviewImageAsset): string {
  return asset.preview_url || asset.public_url || "";
}

function SourceCard({
  asset,
  kind,
  alreadyInLibrary,
  disabled,
  retrying,
  onPromote,
  onRetry,
  onPreview,
}: {
  asset: PreviewImageAsset;
  kind: string;
  alreadyInLibrary: boolean;
  disabled: boolean;
  retrying: boolean;
  onPromote: (assetId: string) => void;
  onRetry?: (mediaAssetId: string) => void;
  onPreview: (url: string) => void;
}) {
  const url = assetUrl(asset);
  const retryable = asset.media_status ? supportsMediaRetry(asset.media_status) : false;
  const ready = asset.media_status === "ready";
  const statusLabel = asset.media_status ? mediaStatusLabel(asset.media_status) : "无状态";
  return (
    <article className="precheck-asset-card precheck-source-card">
      <button
        type="button"
        className="precheck-asset-preview"
        disabled={!url}
        aria-label={`预览${SOURCE_KIND_LABELS[kind] ?? kind}`}
        onClick={() => url && onPreview(url)}
      >
        {url ? (
          <img
            src={url}
            alt={SOURCE_KIND_LABELS[kind] ?? kind}
            loading="lazy"
            onError={(event) => { event.currentTarget.style.visibility = "hidden"; }}
          />
        ) : (
          <span>{statusLabel}</span>
        )}
      </button>
      <div className="precheck-asset-meta">
        <span className="precheck-origin-badge origin-source">
          {SOURCE_KIND_LABELS[kind] ?? kind}
        </span>
        <span className={`precheck-publication status-${asset.media_status || "pending"}`}>
          {statusLabel}
        </span>
      </div>
      <div className="precheck-card-actions">
        {retryable && onRetry ? (
          <button
            type="button"
            disabled={disabled || retrying}
            onClick={() => onRetry(asset.media_asset_id)}
          >
            {retrying ? "重试中…" : "重新同步"}
          </button>
        ) : ready ? (
          <button
            type="button"
            disabled={disabled || alreadyInLibrary}
            onClick={() => onPromote(asset.id)}
          >
            {alreadyInLibrary ? "已在素材库" : "加入素材库"}
          </button>
        ) : (
          <span className="precheck-source-waiting">同步完成后可加入素材库</span>
        )}
      </div>
    </article>
  );
}

export function PrecheckSourcePool({
  assets,
  libraryAssetIds,
  disabled,
  retryingMediaAssetIds = new Set<string>(),
  onPromote,
  onRetry,
  onPreview,
}: PrecheckSourcePoolProps) {
  const byKind = new Map<string, PreviewImageAsset[]>();
  for (const kind of SOURCE_KIND_ORDER) byKind.set(kind, []);
  for (const asset of assets) {
    const kind = asset.source_kind || "";
    if (!byKind.has(kind)) byKind.set(kind, []);
    byKind.get(kind)?.push(asset);
  }

  const knownGroups = SOURCE_KIND_ORDER.filter((kind) => (byKind.get(kind)?.length ?? 0) > 0);
  const extraGroups = Array.from(byKind.keys())
    .filter((kind) => !SOURCE_KIND_ORDER.includes(kind as (typeof SOURCE_KIND_ORDER)[number]))
    .filter((kind) => (byKind.get(kind)?.length ?? 0) > 0);

  return (
    <section className="precheck-manager-section precheck-source-pool">
      <header>
        <div>
          <h3>处理前图片池</h3>
          <p>原图仅作只读对照；点击“加入素材库”后才可参与设主图、轮播与详情。</p>
        </div>
      </header>
      {assets.length === 0 ? (
        <div className="precheck-manager-empty">暂无来源图片。</div>
      ) : (
        [...knownGroups, ...extraGroups].map((kind) => {
          const group = byKind.get(kind) ?? [];
          return (
            <section key={kind} className="precheck-source-group">
              <header>
                <strong>{SOURCE_KIND_LABELS[kind] ?? kind}</strong>
                <span>{group.length} 项</span>
              </header>
              <div className="precheck-asset-grid">
                {group.map((asset) => (
                  <SourceCard
                    key={asset.id}
                    asset={asset}
                    kind={kind}
                    alreadyInLibrary={libraryAssetIds.has(asset.id)}
                    disabled={disabled}
                    retrying={retryingMediaAssetIds.has(asset.media_asset_id)}
                    onPromote={onPromote}
                    onRetry={onRetry}
                    onPreview={onPreview}
                  />
                ))}
              </div>
            </section>
          );
        })
      )}
    </section>
  );
}

export default PrecheckSourcePool;
