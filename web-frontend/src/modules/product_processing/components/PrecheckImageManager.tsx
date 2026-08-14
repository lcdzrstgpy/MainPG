import { useRef, type KeyboardEvent } from "react";
import {
  addAssets,
  moveAsset,
  promoteToLibrary,
  removeAsset,
  removeFromLibrary,
  selectMainAsset,
  type PrecheckImageTarget,
  type RemovedAssetUndo,
} from "../data/precheckImageModel";
import { PrecheckMaterialLibrary } from "./PrecheckMaterialLibrary";
import { PrecheckSourcePool } from "./PrecheckSourcePool";
import type {
  PreviewImageAsset,
  PreviewImageManifest,
} from "../types";

type PrecheckImageManagerProps = {
  assets: PreviewImageAsset[];
  manifest: PreviewImageManifest;
  disabled: boolean;
  onAddFiles: (target: PrecheckImageTarget, files: File[]) => void;
  onManifestChange: (manifest: PreviewImageManifest) => void;
  onPreview: (url: string) => void;
  onUndoAvailable: (undo: RemovedAssetUndo) => void;
  retryingMediaAssetIds?: ReadonlySet<string>;
  onRetryMediaAsset?: (assetId: string) => void;
};

const ORIGIN_LABELS: Record<PreviewImageAsset["origin"], string> = {
  source: "来源图",
  generated: "AI 生成",
  dimension: "尺寸图",
  upload: "本地导入",
};

const PUBLICATION_LABELS: Record<PreviewImageAsset["publication_status"], string> = {
  local: "待发布",
  materializing: "准备中",
  ready: "待发布",
  publishing: "发布中",
  published: "已发布",
  publish_failed: "发布失败",
};

function assetUrl(asset: PreviewImageAsset | undefined): string {
  return asset?.preview_url || asset?.public_url || "";
}

function FilePicker({
  label,
  target,
  disabled,
  onAddFiles,
}: {
  label: string;
  target: PrecheckImageTarget;
  disabled: boolean;
  onAddFiles: PrecheckImageManagerProps["onAddFiles"];
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="btn-mini precheck-add-button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        {label}
      </button>
      <input
        ref={inputRef}
        className="precheck-file-input"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        disabled={disabled}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          if (files.length > 0) onAddFiles(target, files);
        }}
      />
    </>
  );
}

function PreviewAsset({
  asset,
  assetId,
  label,
  onPreview,
}: {
  asset: PreviewImageAsset | undefined;
  assetId: string;
  label: string;
  onPreview: (url: string) => void;
}) {
  const url = assetUrl(asset);
  return (
    <>
      <button
        type="button"
        className="precheck-asset-preview"
        disabled={!url}
        aria-label={`预览${label}`}
        onClick={() => url && onPreview(url)}
      >
        {url ? (
          <img
            src={url}
            alt={label}
            loading="lazy"
            onError={(event) => { event.currentTarget.style.visibility = "hidden"; }}
          />
        ) : (
          <span>无预览</span>
        )}
      </button>
      <div className="precheck-asset-meta">
        <span className={`precheck-origin-badge origin-${asset?.origin ?? "missing"}`}>
          {asset ? ORIGIN_LABELS[asset.origin] : "素材缺失"}
        </span>
        {asset && (
          <span className={`precheck-publication status-${asset.publication_status}`}>
            {PUBLICATION_LABELS[asset.publication_status]}
          </span>
        )}
      </div>
      <small title={assetId}>{assetId}</small>
    </>
  );
}

export function PrecheckImageManager({
  assets,
  manifest,
  disabled,
  onAddFiles,
  onManifestChange,
  onPreview,
  onUndoAvailable,
  retryingMediaAssetIds = new Set<string>(),
  onRetryMediaAsset,
}: PrecheckImageManagerProps) {
  const assetById = new Map(assets.map((asset) => [asset.id, asset]));

  const libraryAssetIds = new Set(manifest.library_asset_ids ?? []);
  const sourceAssets = assets.filter((asset) => asset.bucket === "source");
  const processedAssets = assets.filter((asset) => asset.bucket === "processed");
  const materialAssets = [
    ...processedAssets,
    ...sourceAssets.filter((asset) => libraryAssetIds.has(asset.id)),
  ];

  const remove = (target: PrecheckImageTarget, assetId: string) => {
    const result = removeAsset(manifest, target, assetId);
    onManifestChange(result.manifest);
    onUndoAvailable(result.undo);
  };

  const reorderByKeyboard = (
    event: KeyboardEvent<HTMLElement>,
    target: "carousel" | "detail",
    assetId: string,
  ) => {
    if (!event.altKey) return;
    const previous = event.key === "ArrowLeft" || event.key === "ArrowUp";
    const next = event.key === "ArrowRight" || event.key === "ArrowDown";
    if (!previous && !next) return;
    event.preventDefault();
    onManifestChange(moveAsset(manifest, target, assetId, previous ? -1 : 1));
  };

  const mainAssetId = manifest.main_asset_id;

  return (
    <div className={`precheck-image-manager${disabled ? " is-disabled" : ""}`}>
      <PrecheckSourcePool
        assets={sourceAssets}
        libraryAssetIds={libraryAssetIds}
        disabled={disabled}
        retryingMediaAssetIds={retryingMediaAssetIds}
        onPromote={(assetId) => onManifestChange(promoteToLibrary(manifest, assetId))}
        onRetry={onRetryMediaAsset}
        onPreview={onPreview}
      />

      <PrecheckMaterialLibrary
        assets={materialAssets}
        manifest={manifest}
        disabled={disabled}
        onManifestChange={onManifestChange}
        onRemoveFromLibrary={(assetId) => onManifestChange(removeFromLibrary(manifest, assetId))}
        onPreview={onPreview}
      />

      <section className="precheck-manager-section">
        <header>
          <div>
            <h3>轮播图（第 1 张即主图） <span>{manifest.carousel_asset_ids.length}</span></h3>
            <p>第 1 张即主图；删除或排序后自动更新主图。聚焦卡片可按 Alt + 方向键排序。</p>
          </div>
          <FilePicker
            label="添加图片"
            target="carousel"
            disabled={disabled}
            onAddFiles={onAddFiles}
          />
        </header>
        {manifest.carousel_asset_ids.length === 0 ? (
          <div className="precheck-manager-empty">
            <span>轮播图清单为空（将明确保存为空列表）</span>
            <FilePicker
              label="添加轮播图"
              target="carousel"
              disabled={disabled}
              onAddFiles={onAddFiles}
            />
          </div>
        ) : (
          <div className="precheck-asset-grid output-grid">
            {manifest.carousel_asset_ids.map((assetId, index) => (
              <article
                key={`${assetId}-${index}`}
                className={`precheck-asset-card${mainAssetId === assetId ? " is-selected-main" : ""}`}
                tabIndex={disabled ? -1 : 0}
                aria-label={`轮播图 ${index + 1}，按 Alt 加方向键排序`}
                onKeyDown={(event) => reorderByKeyboard(event, "carousel", assetId)}
              >
                <span className="precheck-order-number">{index + 1}</span>
                {index === 0 && <span className="precheck-main-badge">主图</span>}
                <PreviewAsset
                  asset={assetById.get(assetId)}
                  assetId={assetId}
                  label={`轮播图 ${index + 1}`}
                  onPreview={onPreview}
                />
                <div className="precheck-card-actions sort-actions">
                  <button
                    type="button"
                    aria-label={`轮播图 ${index + 1} 左移`}
                    title="左移"
                    disabled={disabled || index === 0}
                    onClick={() => onManifestChange(moveAsset(manifest, "carousel", assetId, -1))}
                  >←</button>
                  <button
                    type="button"
                    aria-label={`轮播图 ${index + 1} 右移`}
                    title="右移"
                    disabled={disabled || index === manifest.carousel_asset_ids.length - 1}
                    onClick={() => onManifestChange(moveAsset(manifest, "carousel", assetId, 1))}
                  >→</button>
                  <button
                    type="button"
                    disabled={disabled || mainAssetId === assetId}
                    onClick={() => onManifestChange(selectMainAsset(manifest, assetId))}
                  >设主图</button>
                  <button
                    type="button"
                    className="danger"
                    disabled={disabled}
                    onClick={() => remove("carousel", assetId)}
                  >删除</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="precheck-manager-section">
        <header>
          <div>
            <h3>详情图 <span>{manifest.detail_asset_ids.length}</span></h3>
            <p>按顺序追加到产品描述；允许明确保存为空。</p>
          </div>
          <FilePicker
            label="添加图片"
            target="detail"
            disabled={disabled}
            onAddFiles={onAddFiles}
          />
        </header>
        {manifest.detail_asset_ids.length === 0 ? (
          <div className="precheck-manager-empty">
            <span>详情图清单为空（将明确保存为空列表）</span>
            <FilePicker
              label="添加详情图"
              target="detail"
              disabled={disabled}
              onAddFiles={onAddFiles}
            />
          </div>
        ) : (
          <div className="precheck-asset-grid output-grid">
            {manifest.detail_asset_ids.map((assetId, index) => (
              <article
                key={`${assetId}-${index}`}
                className="precheck-asset-card"
                tabIndex={disabled ? -1 : 0}
                aria-label={`详情图 ${index + 1}，按 Alt 加方向键排序`}
                onKeyDown={(event) => reorderByKeyboard(event, "detail", assetId)}
              >
                <span className="precheck-order-number">{index + 1}</span>
                <PreviewAsset
                  asset={assetById.get(assetId)}
                  assetId={assetId}
                  label={`详情图 ${index + 1}`}
                  onPreview={onPreview}
                />
                <div className="precheck-card-actions sort-actions">
                  <button
                    type="button"
                    aria-label={`详情图 ${index + 1} 前移`}
                    title="前移"
                    disabled={disabled || index === 0}
                    onClick={() => onManifestChange(moveAsset(manifest, "detail", assetId, -1))}
                  >←</button>
                  <button
                    type="button"
                    aria-label={`详情图 ${index + 1} 后移`}
                    title="后移"
                    disabled={disabled || index === manifest.detail_asset_ids.length - 1}
                    onClick={() => onManifestChange(moveAsset(manifest, "detail", assetId, 1))}
                  >→</button>
                  <button
                    type="button"
                    className="danger"
                    disabled={disabled}
                    onClick={() => remove("detail", assetId)}
                  >删除</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default PrecheckImageManager;
