import { type FormEvent, useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  loadMyFeedback,
  submitFeedback,
  type FeedbackCategory,
  type FeedbackHistoryItem,
  type FeedbackImagePayload,
} from "../api/personalCenterApi";

const FEEDBACK_MAX_IMAGES = 3;
const FEEDBACK_MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const FEEDBACK_ACCEPT_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

const feedbackCategoryNames: Record<FeedbackCategory, string> = {
  bug: "问题反馈",
  suggestion: "功能建议",
  other: "其他",
};

function formatFeedbackTime(iso: string): string {
  if (!iso) return "";
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  } catch {
    return iso;
  }
}

export function FeedbackPanel() {
  const [content, setContent] = useState("");
  const [category, setCategory] = useState<FeedbackCategory>("suggestion");
  const [contact, setContact] = useState("");
  const [images, setImages] = useState<FeedbackImagePayload[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [history, setHistory] = useState<FeedbackHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const data = await loadMyFeedback(50, 0);
      setHistory(Array.isArray(data.feedback) ? data.feedback : []);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "反馈记录加载失败");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  // 打开抽屉时重新拉取，避免展示过期状态；Esc 关闭。
  useEffect(() => {
    if (!historyOpen) return;
    void loadHistory();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHistoryOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [historyOpen, loadHistory]);

  const handleImagePick = useCallback(
    (files: FileList | null) => {
      if (!files || !files.length) return;
      setError("");
      const room = FEEDBACK_MAX_IMAGES - images.length;
      if (room <= 0) {
        setError(`最多上传 ${FEEDBACK_MAX_IMAGES} 张图片`);
        return;
      }
      const picked = Array.from(files).slice(0, room);
      const invalid = picked.find((f) => !FEEDBACK_ACCEPT_MIMES.includes(f.type));
      if (invalid) {
        setError("仅支持 PNG / JPG / GIF / WebP 图片");
        return;
      }
      const oversized = picked.find((f) => f.size > FEEDBACK_MAX_IMAGE_BYTES);
      if (oversized) {
        setError(`单张图片不能超过 2MB（${oversized.name} 过大）`);
        return;
      }
      Promise.all(
        picked.map(
          (file) =>
            new Promise<FeedbackImagePayload>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => {
                const dataUrl = String(reader.result || "");
                const comma = dataUrl.indexOf(",");
                resolve({
                  name: file.name,
                  mime: file.type || "image/png",
                  data_b64: comma >= 0 ? dataUrl.slice(comma + 1) : "",
                });
              };
              reader.onerror = () => reject(new Error("read failed"));
              reader.readAsDataURL(file);
            }),
        ),
      )
        .then((loaded) => setImages((prev) => [...prev, ...loaded]))
        .catch(() => setError("读取图片失败，请重试"));
    },
    [images.length],
  );

  const removeImage = useCallback((index: number) => {
    setImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleSubmit = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      const text = content.trim();
      if (!text) {
        setError("请先填写反馈内容");
        return;
      }
      setSubmitting(true);
      setError("");
      setSuccess("");
      try {
        await submitFeedback({ content: text, category, contact: contact.trim(), images });
        setSuccess("反馈已提交，感谢你的建议！");
        setContent("");
        setContact("");
        setImages([]);
        void loadHistory();
      } catch (err) {
        const raw = err instanceof Error ? err.message : "";
        setError(
          raw.includes("too many feedback")
            ? "提交太频繁啦，6 小时内最多 10 条反馈，请稍后再试"
            : raw || "提交失败，请稍后重试",
        );
      } finally {
        setSubmitting(false);
      }
    },
    [content, category, contact, images, loadHistory],
  );

  return (
    <article className="personal-card feedback-card">
      <div className="personal-card-title">
        <span className="iconfont icon-message" aria-hidden="true" />
        <div><h2>意见反馈</h2><small>遇到的问题或想要的功能，都可以告诉我们（最多 3 张图片，每张不超过 2MB）</small></div>
      </div>

      <label className="feedback-field">
        <textarea
          className="feedback-textarea"
          rows={4}
          maxLength={2000}
          placeholder="描述你遇到的问题，或写下你的建议…"
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
        <span className="feedback-count">{content.length} / 2000</span>
      </label>

      {images.length > 0 && (
        <div className="feedback-images">
          {images.map((image, index) => (
            <div className="feedback-image-item" key={`${image.name}-${index}`}>
              <img src={`data:${image.mime};base64,${image.data_b64}`} alt={image.name} />
              <button type="button" className="feedback-image-remove" onClick={() => removeImage(index)} aria-label={`移除 ${image.name}`}>×</button>
            </div>
          ))}
          {images.length < FEEDBACK_MAX_IMAGES && (
            <label className="feedback-image-add">
              + 添加图片
              <input
                type="file"
                accept={FEEDBACK_ACCEPT_MIMES.join(",")}
                multiple
                className="feedback-file-input"
                onChange={(event) => {
                  handleImagePick(event.target.files);
                  event.target.value = "";
                }}
              />
            </label>
          )}
        </div>
      )}
      {images.length === 0 && (
        <label className="feedback-image-add is-standalone">
          + 添加图片（可选）
          <input
            type="file"
            accept={FEEDBACK_ACCEPT_MIMES.join(",")}
            multiple
            className="feedback-file-input"
            onChange={(event) => {
              handleImagePick(event.target.files);
              event.target.value = "";
            }}
          />
        </label>
      )}

      <div className="feedback-meta-row">
        <label className="feedback-field is-inline">
          <span>类型</span>
          <select value={category} onChange={(event) => setCategory(event.target.value as FeedbackCategory)}>
            <option value="suggestion">功能建议</option>
            <option value="bug">问题反馈</option>
            <option value="other">其他</option>
          </select>
        </label>
        <label className="feedback-field is-inline is-grow">
          <span>联系方式（选填）</span>
          <input
            type="text"
            maxLength={200}
            placeholder="微信号 / 邮箱 / 手机号，方便我们联系你"
            value={contact}
            onChange={(event) => setContact(event.target.value)}
          />
        </label>
      </div>

      <div className="feedback-actions">
        <button type="button" className="feedback-submit" onClick={() => handleSubmit()} disabled={submitting}>
          {submitting ? "提交中…" : "提交反馈"}
        </button>
        {success && <span className="feedback-state is-success">{success}</span>}
        {error && <span className="feedback-state is-error">{error}</span>}
        <button type="button" className="feedback-history-trigger" onClick={() => setHistoryOpen(true)}>
          我的反馈
          {history.length > 0 && <b>{history.length}</b>}
        </button>
      </div>

      {historyOpen && createPortal(
        <div className="feedback-drawer-root">
          <div className="feedback-drawer-mask" onClick={() => setHistoryOpen(false)} />
          <aside className="feedback-drawer" role="dialog" aria-modal="true" aria-label="我的反馈">
            <header className="feedback-drawer-head">
              <div>
                <h2>我的反馈</h2>
                <p>共 {history.length} 条 · 仅保留最近 14 天</p>
              </div>
              <button type="button" className="feedback-drawer-close" onClick={() => setHistoryOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="feedback-drawer-body">
              {historyLoading && <p className="feedback-history-state">正在加载…</p>}
              {historyError && <p className="feedback-history-state is-error">{historyError}</p>}
              {!historyLoading && !historyError && history.length === 0 && (
                <p className="feedback-history-state">还没有提交过反馈</p>
              )}
              {!historyLoading && history.length > 0 && (
                <ul className="feedback-list">
                  {history.map((item) => (
                    <li key={item.feedback_id} className="feedback-list-item">
                      <div className="feedback-list-head">
                        <span className={`feedback-status is-${item.status}`}>
                          {item.status === "new" ? "待处理" : item.status === "processing" ? "处理中" : "已解决"}
                        </span>
                        <span className="feedback-list-category">{feedbackCategoryNames[item.category] ?? item.category}</span>
                        <span className="feedback-list-time">{formatFeedbackTime(item.created_at)}</span>
                        {item.image_count > 0 && <span className="feedback-list-images">含 {item.image_count} 张图</span>}
                      </div>
                      <p className="feedback-list-content">{item.content}</p>
                      {item.admin_note && <p className="feedback-list-note">官方回复：{item.admin_note}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>,
        document.body,
      )}
    </article>
  );
}
