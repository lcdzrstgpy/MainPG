import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { showToast } from "../../../shared/components/toastStore";
import {
  deleteMobileAsset,
  loadMobileAssetDetail,
  loadMobileAssets,
  type MobileAssetMeta,
} from "../api/personalAssetsApi";
import "../styles/personalAssets.css";

const PAGE_SIZE = 48;

/** 「一键送入业务」目标：这些模块都以本地图片作为输入，下载后拖入即可使用。 */
const SEND_TARGETS: Array<{ id: WorkspaceModuleId; label: string }> = [
  { id: "combo_generate", label: "组合生图" },
  { id: "pod_semi_customization", label: "POD 印花创作" },
  { id: "pod_replica", label: "爆款复刻" },
];

function formatSize(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

/** 后端按 UTC（YYYY-MM-DD HH:MM:SS）落库，补 Z 后按本地时区展示。 */
function formatTime(raw: string): string {
  const value = String(raw ?? "").trim();
  if (!value) return "";
  const iso = value.includes("T") ? value : value.replace(" ", "T");
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function typeLabel(type: string): string {
  if (type === "photo") return "拍照";
  if (type === "gallery") return "相册";
  return "文件";
}

function b64ToBlob(b64: string, mime: string): Blob {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime || "application/octet-stream" });
}

/** 触发一次浏览器下载（用于「下载」与「送入业务」）。 */
function triggerDownload(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName || "asset.png";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** 网格缩略图：进入视口才拉详情（详情含 base64 原图），避免一次性加载整页大图。 */
function AssetThumb({ assetId, mime, alt }: { assetId: string; mime: string; alt: string }) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const [src, setSrc] = useState("");

  useEffect(() => {
    const node = holderRef.current;
    if (!node) return;
    let cancelled = false;
    const run = () => {
      loadMobileAssetDetail(assetId)
        .then((res) => {
          if (cancelled) return;
          const detail = res.asset;
          setSrc(`data:${detail.mime || mime || "image/png"};base64,${detail.data_b64}`);
        })
        .catch(() => {
          /* 缩略图加载失败静默处理，点击卡片仍可看详情错误提示 */
        });
    };
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer.disconnect();
          run();
        }
      },
      { rootMargin: "240px" },
    );
    observer.observe(node);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [assetId, mime]);

  return (
    <div className="asset-thumb" ref={holderRef}>
      {src ? (
        <img src={src} alt={alt} loading="lazy" />
      ) : (
        <i className="iconfont icon-image asset-thumb-placeholder" />
      )}
    </div>
  );
}

export function PersonalAssetsPage({ onOpenModule }: { onOpenModule: (id: WorkspaceModuleId) => void }) {
  const [assets, setAssets] = useState<MobileAssetMeta[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [preview, setPreview] = useState<{ meta: MobileAssetMeta; dataUrl: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    setError("");
    try {
      const res = await loadMobileAssets(PAGE_SIZE, 0);
      setAssets(res.assets ?? []);
      setTotal(res.total ?? 0);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openPreview = async (asset: MobileAssetMeta) => {
    setPreview({ meta: asset, dataUrl: "" });
    setPreviewLoading(true);
    try {
      const res = await loadMobileAssetDetail(asset.asset_id);
      const detail = res.asset;
      setPreview((current) =>
        current && current.meta.asset_id === asset.asset_id
          ? { meta: asset, dataUrl: `data:${detail.mime || "image/png"};base64,${detail.data_b64}` }
          : current,
      );
    } catch (cause) {
      setPreview(null);
      showToast(cause instanceof Error ? cause.message : "预览失败", "error");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleDownload = async (asset: MobileAssetMeta) => {
    if (busyId) return;
    setBusyId(asset.asset_id);
    try {
      const res = await loadMobileAssetDetail(asset.asset_id);
      const detail = res.asset;
      triggerDownload(b64ToBlob(detail.data_b64, detail.mime), detail.file_name || `${asset.asset_id}.png`);
      showToast(`已下载「${detail.file_name || "图片"}」`, "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "下载失败", "error");
    } finally {
      setBusyId("");
    }
  };

  const handleDelete = async (asset: MobileAssetMeta) => {
    if (busyId) return;
    if (!window.confirm(`确定删除「${asset.file_name || "该图片"}」吗？删除后不可恢复。`)) return;
    setBusyId(asset.asset_id);
    try {
      await deleteMobileAsset(asset.asset_id);
      setPreview((current) => (current && current.meta.asset_id === asset.asset_id ? null : current));
      setAssets((current) => current.filter((item) => item.asset_id !== asset.asset_id));
      setTotal((current) => Math.max(0, current - 1));
      showToast("已删除", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "删除失败", "error");
    } finally {
      setBusyId("");
    }
  };

  const handleSendTo = async (asset: MobileAssetMeta, target: { id: WorkspaceModuleId; label: string }) => {
    if (busyId) return;
    setBusyId(asset.asset_id);
    try {
      const res = await loadMobileAssetDetail(asset.asset_id);
      const detail = res.asset;
      triggerDownload(b64ToBlob(detail.data_b64, detail.mime), detail.file_name || `${asset.asset_id}.png`);
      setPreview(null);
      onOpenModule(target.id);
      showToast(`图片已下载，并已打开「${target.label}」，把文件拖入上传区即可使用`, "success", 6000);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "操作失败", "error");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="personal-assets-page">
      <header className="personal-assets-head">
        <div className="personal-assets-title">
          <h2>个人资产</h2>
          <p>手机端拍照 / 相册上传的互通图片，同一账号在 PC 端即可查看、下载或送入业务。</p>
        </div>
        <div className="personal-assets-head-actions">
          <span className="personal-assets-count">共 {total} 张</span>
          <button
            type="button"
            className="personal-assets-btn ghost"
            onClick={() => void load()}
            disabled={refreshing}
          >
            {refreshing ? "刷新中…" : "刷新"}
          </button>
        </div>
      </header>

      {loading ? (
        <div className="personal-assets-state">正在加载…</div>
      ) : error ? (
        <div className="personal-assets-state error">{error}</div>
      ) : assets.length === 0 ? (
        <div className="personal-assets-empty">
          <i className="iconfont icon-image" />
          <p>还没有手机端上传的图片</p>
          <span>在手机 App 里用「拍照」页拍照或从相册选择，上传后这里就会出现。</span>
        </div>
      ) : (
        <div className="personal-assets-grid">
          {assets.map((asset) => (
            <article key={asset.asset_id} className="asset-card">
              <button
                type="button"
                className="asset-card-thumb-btn"
                onClick={() => void openPreview(asset)}
                title="点击预览"
              >
                <AssetThumb assetId={asset.asset_id} mime={asset.mime} alt={asset.file_name} />
                <span className="asset-card-type">{typeLabel(asset.asset_type)}</span>
              </button>
              <div className="asset-card-meta">
                <span className="asset-card-name" title={asset.file_name}>
                  {asset.file_name || "未命名图片"}
                </span>
                <span className="asset-card-sub">
                  {formatSize(asset.size_bytes)} · {formatTime(asset.created_at)}
                </span>
              </div>
              <div className="asset-card-actions">
                <button
                  type="button"
                  className="personal-assets-btn tiny"
                  onClick={() => void handleDownload(asset)}
                  disabled={busyId === asset.asset_id}
                >
                  下载
                </button>
                <button
                  type="button"
                  className="personal-assets-btn tiny danger"
                  onClick={() => void handleDelete(asset)}
                  disabled={busyId === asset.asset_id}
                >
                  删除
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {preview
        ? createPortal(
            <div className="asset-preview-layer" onClick={() => setPreview(null)}>
              <div className="asset-preview-panel" onClick={(event) => event.stopPropagation()}>
                <button
                  type="button"
                  className="asset-preview-close"
                  onClick={() => setPreview(null)}
                  aria-label="关闭"
                >
                  ×
                </button>
                <div className="asset-preview-stage">
                  {previewLoading && !preview.dataUrl ? (
                    <span className="asset-preview-loading">加载中…</span>
                  ) : preview.dataUrl ? (
                    <img src={preview.dataUrl} alt={preview.meta.file_name} />
                  ) : (
                    <span className="asset-preview-loading">无法加载图片</span>
                  )}
                </div>
                <div className="asset-preview-info">
                  <div className="asset-preview-name">{preview.meta.file_name || "未命名图片"}</div>
                  <ul className="asset-preview-source">
                    <li>来源：手机端 · {typeLabel(preview.meta.asset_type)}</li>
                    <li>机型：{preview.meta.device_name || "未知"}</li>
                    <li>平台：{preview.meta.platform || "-"}{preview.meta.app_version ? ` · v${preview.meta.app_version}` : ""}</li>
                    <li>大小：{formatSize(preview.meta.size_bytes)}</li>
                    <li>上传时间：{formatTime(preview.meta.created_at)}</li>
                    {preview.meta.source_site ? <li>来源站点：{preview.meta.source_site}</li> : null}
                    {preview.meta.note ? <li>备注：{preview.meta.note}</li> : null}
                  </ul>
                  <div className="asset-preview-actions">
                    <button
                      type="button"
                      className="personal-assets-btn"
                      onClick={() => void handleDownload(preview.meta)}
                      disabled={busyId === preview.meta.asset_id}
                    >
                      下载
                    </button>
                    <span className="asset-send-group">
                      <span className="asset-send-label">送入业务：</span>
                      {SEND_TARGETS.map((target) => (
                        <button
                          key={target.id}
                          type="button"
                          className="personal-assets-btn tiny"
                          onClick={() => void handleSendTo(preview.meta, target)}
                          disabled={busyId === preview.meta.asset_id}
                        >
                          {target.label}
                        </button>
                      ))}
                    </span>
                    <button
                      type="button"
                      className="personal-assets-btn danger"
                      onClick={() => void handleDelete(preview.meta)}
                      disabled={busyId === preview.meta.asset_id}
                    >
                      删除
                    </button>
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
