import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listMaterials, uploadMaterial, type MaterialUploadProgress } from "../api/endpoints/materials";
import type { PublicLocalMaterial } from "../api/types";
import { MATERIAL_ACCEPT, MATERIAL_MAX_BYTES, materialDisplayName } from "../lib/material-library";
import { aiVideoMediaUrl } from "../lib/media-url";

type UploadRow = {
  name: string;
  state: "waiting" | "uploading" | "done" | "duplicate" | "cancelled" | "failed";
  error?: string;
};

const PAGE_SIZE = 12;

type LocalMaterialLibraryProps = {
  projectId: string;
  shots: Array<{ shotId: number; description: string }>;
  refreshKey: number;
  disabled?: boolean;
  onUse: (material: PublicLocalMaterial, shotId: number) => Promise<void>;
  onFill: () => Promise<void>;
};

/** 项目本地素材库：上传 1–12 个素材、筛选/预览，并套用到指定镜头。 */
export function LocalMaterialLibrary({
  projectId,
  shots,
  refreshKey,
  disabled = false,
  onUse,
  onFill,
}: LocalMaterialLibraryProps) {
  const [items, setItems] = useState<PublicLocalMaterial[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [mediaType, setMediaType] = useState<"all" | "video" | "image">("all");
  const [page, setPage] = useState(0);
  const [targetShot, setTargetShot] = useState("");
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [busyMaterial, setBusyMaterial] = useState<string | null>(null);
  const [filling, setFilling] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    try {
      const result = await listMaterials(projectId);
      if (!alive.current) return;
      setItems(result.materials ?? []);
      setLoadError("");
    } catch (cause) {
      if (alive.current) setLoadError(cause instanceof Error ? cause.message : "素材库加载失败");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    void reload();
    return () => {
      alive.current = false;
      uploadAbort.current?.abort();
    };
  }, [reload, refreshKey]);

  const filtered = useMemo(() => {
    const keyword = query.trim().normalize("NFKC").toLowerCase();
    return items
      .filter((item) => mediaType === "all" || item.mediaType === mediaType)
      .filter((item) =>
        keyword
          ? `${item.originalName} ${item.tags.join(" ")}`.normalize("NFKC").toLowerCase().includes(keyword)
          : true,
      )
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }, [items, mediaType, query]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const visible = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const editBusy = disabled || uploading || filling || busyMaterial !== null;

  function updateRow(index: number, patch: Partial<UploadRow>) {
    if (!alive.current) return;
    setUploads((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function uploadFiles(files: File[]) {
    if (uploadAbort.current) return;
    if (files.length > 12) {
      setNotice({ kind: "error", text: "一次最多上传 12 个素材" });
      return;
    }
    if (!files.length) return;
    setUploading(true);
    setNotice(null);
    setUploads(files.map((file) => ({ name: file.name, state: "waiting" })));
    const controller = new AbortController();
    uploadAbort.current = controller;
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        if (controller.signal.aborted) {
          updateRow(index, { state: "cancelled" });
          continue;
        }
        if (file.size > MATERIAL_MAX_BYTES) {
          updateRow(index, { state: "failed", error: "超过 80MB" });
          continue;
        }
        updateRow(index, { state: "uploading" });
        try {
          const result = await uploadMaterial(projectId, file, {
            signal: controller.signal,
            onProgress: (progress: MaterialUploadProgress) => {
              if (progress.percent < 100) return;
              updateRow(index, { state: "uploading" });
            },
          });
          updateRow(index, { state: result.duplicate ? "duplicate" : "done" });
        } catch (cause) {
          updateRow(index, {
            state: controller.signal.aborted ? "cancelled" : "failed",
            error: cause instanceof Error ? cause.message : "上传失败",
          });
        }
      }
    } finally {
      uploadAbort.current = null;
      if (alive.current) {
        setUploading(false);
        await reload();
      }
    }
  }

  async function useMaterial(item: PublicLocalMaterial) {
    const shotId = Number(targetShot);
    if (!Number.isFinite(shotId) || editBusy) return;
    setBusyMaterial(item.name);
    setNotice(null);
    try {
      await onUse(item, shotId);
      setNotice({ kind: "ok", text: `已套用到第 ${shotId} 镜` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "套用素材失败" });
    } finally {
      setBusyMaterial(null);
    }
  }

  async function fillUnlocked() {
    if (editBusy || !items.length) return;
    setFilling(true);
    setNotice(null);
    try {
      await onFill();
      setNotice({ kind: "ok", text: "已用本地素材填充未锁定镜头" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "填充失败" });
    } finally {
      setFilling(false);
    }
  }

  return (
    <details className="ai-video-material-library" aria-label="本地素材库">
      <summary>
        <strong>本地素材库</strong>
        <span>{items.length}</span>
      </summary>

      <div className="ai-video-material-body">
        <p className="ai-video-settings-message">
          上传 1–12 个素材（≤80MB），选择镜头后套用；锁定的镜头不会被自动填充覆盖。
        </p>
        <div className="ai-video-material-actions">
          <button type="button" className="is-secondary" disabled={uploading} onClick={() => inputRef.current?.click()}>
            {uploading ? "上传中…" : "上传素材"}
          </button>
          {uploading && (
            <button type="button" className="is-secondary" onClick={() => uploadAbort.current?.abort()}>
              取消上传
            </button>
          )}
          <button type="button" className="is-secondary" disabled={editBusy || !items.length} onClick={() => void fillUnlocked()}>
            {filling ? "填充中…" : "用素材填充未锁定镜头"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept={MATERIAL_ACCEPT}
            multiple
            hidden
            aria-label="上传素材"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              void uploadFiles(files);
            }}
          />
        </div>

        {uploads.length > 0 && (
          <ul className="ai-video-material-uploads">
            {uploads.map((row, index) => (
              <li key={`${row.name}-${index}`}>
                <span>{row.name}</span>
                <em>
                  {row.error ||
                    (row.state === "waiting"
                      ? "排队中"
                      : row.state === "uploading"
                        ? "上传中…"
                        : row.state === "done"
                          ? "已上传"
                          : row.state === "duplicate"
                            ? "重复（已存在）"
                            : row.state === "cancelled"
                              ? "已取消"
                              : "失败")}
                </em>
              </li>
            ))}
          </ul>
        )}

        {notice && (
          <p className={notice.kind === "error" ? "ai-video-field-error" : "ai-video-settings-message"} role="status">
            {notice.text}
          </p>
        )}

        {loading ? (
          <p className="ai-video-settings-message">正在读取素材库…</p>
        ) : loadError ? (
          <div className="ai-video-settings-message" role="alert">
            <span>{loadError}</span>
            <button type="button" className="is-secondary" onClick={() => void reload()}>
              重试
            </button>
          </div>
        ) : (
          <>
            <div className="ai-video-material-filters">
              <input
                type="search"
                aria-label="搜索素材"
                placeholder="搜索文件名或标签"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(0);
                }}
              />
              <select
                aria-label="素材类型"
                value={mediaType}
                onChange={(event) => {
                  setMediaType(event.target.value as "all" | "video" | "image");
                  setPage(0);
                }}
              >
                <option value="all">全部</option>
                <option value="video">视频</option>
                <option value="image">图片</option>
              </select>
              <select aria-label="目标镜头" value={targetShot} onChange={(event) => setTargetShot(event.target.value)}>
                <option value="">选择目标镜头</option>
                {shots.map((shot) => (
                  <option key={shot.shotId} value={shot.shotId}>
                    第 {shot.shotId} 镜 · {shot.description.slice(0, 40)}
                  </option>
                ))}
              </select>
            </div>

            {visible.length === 0 ? (
              <p className="ai-video-settings-message">{items.length ? "没有匹配的素材" : "素材库还是空的，先上传几个素材。"}</p>
            ) : (
              <div className="ai-video-material-grid">
                {visible.map((item) => {
                  const url = aiVideoMediaUrl(item.url);
                  const isVideo = item.mediaType === "video";
                  return (
                    <article key={item.name} className="ai-video-material-card">
                      <div className="ai-video-material-thumb">
                        {preview === item.name && url ? (
                          isVideo ? (
                            <video src={url} controls playsInline preload="metadata" />
                          ) : (
                            <img src={url} alt={item.originalName} loading="lazy" />
                          )
                        ) : isVideo ? (
                          <span>▶ 视频</span>
                        ) : url ? (
                          <img src={url} alt={item.originalName} loading="lazy" />
                        ) : (
                          <span>图片</span>
                        )}
                      </div>
                      <p className="ai-video-material-name">{materialDisplayName(item.originalName)}</p>
                      <span className="ai-video-asset-meta">
                        {isVideo ? "视频" : "图片"} · {(item.sizeBytes / 1024 / 1024).toFixed(1)} MB
                        {item.width && item.height ? ` · ${item.width}×${item.height}` : ""}
                      </span>
                      {item.tags.length > 0 && <span className="ai-video-asset-meta">{item.tags.join(" · ")}</span>}
                      <div className="ai-video-asset-actions">
                        <button
                          type="button"
                          className="is-secondary"
                          onClick={() => setPreview(preview === item.name ? null : item.name)}
                        >
                          {preview === item.name ? "收起" : "预览"}
                        </button>
                        <button
                          type="button"
                          disabled={editBusy || !targetShot}
                          onClick={() => void useMaterial(item)}
                        >
                          {busyMaterial === item.name ? "套用中…" : "套用到镜头"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}

            <div className="ai-video-material-pager">
              <span>
                共 {filtered.length} 个 · 第 {currentPage + 1}/{pages} 页
              </span>
              <div>
                <button
                  type="button"
                  className="is-secondary"
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  上一页
                </button>
                <button
                  type="button"
                  className="is-secondary"
                  disabled={currentPage + 1 >= pages}
                  onClick={() => setPage(currentPage + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </details>
  );
}
