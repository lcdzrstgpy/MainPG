import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DOMPurify from "dompurify";
import { Marked } from "marked";

import {
  PRIVACY_POLICY_URL_FALLBACK,
  readCachedPrivacyPolicy,
  syncPrivacyPolicy,
  type PrivacyPolicyDocument,
} from "../api/privacyPolicyApi";

/**
 * 隐私政策内嵌阅读。
 *
 * 规则（2026-11-03 与产品确认）：
 * - 登录页点「隐私政策」「《界野隐私政策》」不再跳外链，而是就地内嵌阅读；
 * - **每次打开**都拉一次远端，并与本地缓存比对版本：版本不同就更新缓存
 *   （最新版留存在本地，作为下次离线时的兜底）；
 * - 远端不可用时直接读本地缓存，不阻塞阅读；两者都没有才提示去浏览器打开；
 * - 顶部始终保留「在浏览器打开」，外链照旧可用。
 */

type PrivacyPolicyDialogProps = {
  open: boolean;
  onClose: () => void;
  /** 拿到（或更新了）远端文档时回调，父组件据此刷新版本号与同意状态。 */
  onDocument?: (document: PrivacyPolicyDocument) => void;
};

const ALLOWED_TAGS = [
  "p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "u", "a",
  "blockquote", "table", "thead", "tbody", "tr", "th", "td", "br", "hr",
  "code", "pre", "span", "div",
];
const ALLOWED_ATTR = ["href", "id", "class", "title"];

/** 服务端 body_markdown 用了 Python-Markdown 的 attr_list 语法，通用渲染器不认，先剥掉。 */
function stripAttrList(source: string): string {
  return source
    .replace(/\{:[^}]*\}/g, "")
    .replace(/\{#[^}]*\}/g, "")
    .replace(/\{\.([a-z-]+)\}/g, "");
}

const marked = new Marked({ gfm: true, breaks: true });

function renderBody(document: PrivacyPolicyDocument | null): string {
  if (!document) return "";
  const rawHtml = typeof document.body_html === "string" ? document.body_html : "";
  const html = rawHtml.trim() ? rawHtml : (marked.parse(stripAttrList(document.body_markdown ?? "")) as string);
  if (!html.trim()) return "";
  return DOMPurify.sanitize(html, { ALLOWED_TAGS, ALLOWED_ATTR });
}

function formatUpdated(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function PrivacyPolicyDialog({ open, onClose, onDocument }: PrivacyPolicyDialogProps) {
  const [policyDoc, setPolicyDoc] = useState<PrivacyPolicyDocument | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "empty">("loading");
  const [fromCache, setFromCache] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  // 回调放 ref：避免父组件每次渲染生成新函数导致重复拉取
  const onDocumentRef = useRef(onDocument);
  onDocumentRef.current = onDocument;

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const cached = readCachedPrivacyPolicy();
    setPolicyDoc(cached);
    setStatus(cached ? "ready" : "loading");
    setFromCache(false);
    void syncPrivacyPolicy().then((result) => {
      if (!alive) return;
      if (result.document) {
        setPolicyDoc(result.document);
        setStatus("ready");
        setFromCache(result.fromCache);
        // 每次都通知父组件：它会用「当前远端版本」重判「已同意」是否仍有效，
        // 幂等且不会误伤——只要版本没变就不会改变勾选状态。
        onDocumentRef.current?.(result.document);
      } else if (!cached) {
        setStatus("empty");
      }
    });
    return () => {
      alive = false;
    };
  }, [open]);

  // Esc 关闭 + 打开期间锁背景滚动
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  const html = useMemo(() => renderBody(policyDoc), [policyDoc]);

  // 正文里的链接一律新窗口打开，并补 rel
  useEffect(() => {
    if (!open) return;
    const root = bodyRef.current;
    if (!root) return;
    root.querySelectorAll("a").forEach((a) => {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    });
  }, [open, html]);

  if (!open) return null;

  const publicUrl = typeof policyDoc?.public_url === "string" && policyDoc.public_url.trim()
    ? policyDoc.public_url.trim()
    : PRIVACY_POLICY_URL_FALLBACK;
  const version = typeof policyDoc?.version === "string" ? policyDoc.version : "";
  const effective = typeof policyDoc?.effective_date === "string" ? policyDoc.effective_date : "";
  const updated = formatUpdated(policyDoc?.updated_at);

  return createPortal(
    <div className="privacy-dialog-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="privacy-dialog" role="dialog" aria-modal="true" aria-labelledby="privacy-dialog-title">
        <header className="privacy-dialog-head">
          <div className="privacy-dialog-heading">
            <span className="privacy-dialog-eyebrow">PRIVACY POLICY</span>
            <h2 id="privacy-dialog-title">{policyDoc?.title?.trim() || "界野隐私政策"}</h2>
            <p className="privacy-dialog-meta">
              {version && <span>版本：{version}</span>}
              {effective && <span>生效日期：{effective}</span>}
              {updated && <span>更新于 {updated}</span>}
              {fromCache && <span className="is-cache">离线 · 本地留存版本</span>}
            </p>
          </div>
          <div className="privacy-dialog-actions">
            <a className="privacy-dialog-link" href={publicUrl} target="_blank" rel="noopener noreferrer">
              在浏览器打开
            </a>
            <button type="button" className="privacy-dialog-close" onClick={onClose} aria-label="关闭隐私政策">
              ×
            </button>
          </div>
        </header>

        <div className="privacy-dialog-body">
          {status === "loading" && <p className="privacy-dialog-placeholder">正在加载隐私政策…</p>}
          {status === "empty" && (
            <p className="privacy-dialog-placeholder">
              暂时无法加载隐私政策（网络不可用，本地也没有留存副本）。
              <br />
              请接入网络后重试，或
              <a href={publicUrl} target="_blank" rel="noopener noreferrer">在浏览器打开</a>。
            </p>
          )}
          {status === "ready" && (
            <article
              className="privacy-doc"
              ref={bodyRef}
              // eslint-disable-next-line react/no-danger -- 内容来自平台服务端且已过 DOMPurify 白名单
              dangerouslySetInnerHTML={{ __html: html }}
            />
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}
