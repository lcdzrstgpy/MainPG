import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { getAuthAccount } from "../../../transport/http/client";
import { PodBriefInput } from "../../pod_customization/components/PodBriefInput";
import {
  briefFieldsToDraft,
  createBriefHistoryItem,
  mergeBusinessFields,
  recordBriefHistory,
} from "../../pod_customization/data/podBrief";
import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import type {
  PodBriefHistoryItem,
  PodBusinessFieldsDraft,
} from "../../pod_customization/types";
import { podSemiCustomizationApi, blobForPath, downloadBlobAs } from "../api/podSemiCustomizationApi";
import { PodSemiBatchDrawer } from "../components/PodSemiBatchDrawer";
import { PodSemiResultLightbox } from "../components/PodSemiResultLightbox";
import {
  SEMI_BATCH_COUNTS,
  isSemiBatchPaused,
  isSemiBatchRunning,
  isSemiBatchTerminal,
  isSemiCountValid,
  semiBatchStatusLabel,
  semiBusinessFieldsForApi,
  semiGroupCount,
  semiItemStatusLabel,
  semiItemTag,
} from "../data/podSemiCustomizationModel";
import {
  createEmptyPodSemiDraft,
  loadPodSemiDraft,
  savePodSemiDraft,
} from "../data/podSemiCustomizationDraft";
import type { SemiBatch } from "../types";
import "../styles/podSemiCustomization.css";

type Props = {
  isActive?: boolean;
};

type PodDraftAccount = {
  account_id?: string;
  customer_id?: string;
  workspace_id?: string;
  workspace_code?: string;
};

/**
 * 半定制是纯图案花色制作，与具体产品无关，因此只保留图案相关字段：
 * 主题风格 / 元素关键词 / 配色 / 禁用元素。产品名、品类、市场、人群、卖点
 * 对纯图案生成没有意义，一律不展示（后端 SemiBatchCreate 也不校验它们）。
 */
const SEMI_FIELDS: Array<{
  key: keyof PodBusinessFieldsDraft;
  label: string;
  required?: boolean;
  hint?: string;
}> = [
  { key: "design_theme", label: "主题整批统一风格", required: true, hint: "整批统一的图案风格基调，例如：美式西南复古、复古手绘插画风、法式田园碎花" },
  { key: "style_keywords", label: "元素关键词", required: true, hint: "用顿号或逗号分隔；每一项都要是具体事物（如向日葵、雏菊、藤蔓），不要写形容词或风格词；系统按组随机分配主打/辅主/点缀" },
  { key: "color_preferences", label: "偏好配色", hint: "尽量多写具体颜色名，如「电光粉紫、落日金橙、霓虹青」；颜色越多跨组差异越明显" },
  { key: "excluded_elements", label: "禁用元素", hint: "覆盖侵权类（品牌 logo、卡通 IP、名人肖像）与危险违禁类（武器、毒品、仇恨符号），避免商品下架或店铺被封" },
];

/** 文本框高度随内容自适应：先归零再按 scrollHeight 撑开（与全定制页同一手法）。 */
function autoGrowTextarea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.max(34, textarea.scrollHeight)}px`;
}

const POLL_INTERVAL_MS = 2_000;
const NOTICE_AUTO_DISMISS_MS = 6_000;

export function PodSemiCustomizationPage({ isActive = true }: Props) {
  const draftScope = useMemo(() => {
    const account = getAuthAccount<PodDraftAccount>();
    const accountId = (account?.account_id || account?.customer_id)?.trim() ?? "";
    const workspaceId = (account?.workspace_id || account?.workspace_code)?.trim() ?? "";
    return accountId && workspaceId ? { accountId, workspaceId } : null;
  }, []);

  const [initialDraft] = useState(() => draftScope
    ? loadPodSemiDraft(draftScope.accountId, draftScope.workspaceId)
    : createEmptyPodSemiDraft());
  const [businessFields, setBusinessFields] = useState<PodBusinessFieldsDraft>(initialDraft.business_fields);
  const [creativePrompt, setCreativePrompt] = useState(initialDraft.creative_prompt);
  const [count, setCount] = useState<number>(initialDraft.count);
  const [briefHistory, setBriefHistory] = useState<PodBriefHistoryItem[]>(initialDraft.brief_history);

  const [batches, setBatches] = useState<SemiBatch[]>([]);
  const [activeBatch, setActiveBatch] = useState<SemiBatch | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [batchesLoading, setBatchesLoading] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const noticeTimerRef = useRef<number | undefined>(undefined);
  const fieldTextareasRef = useRef<Array<HTMLTextAreaElement | null>>([]);

  const showNotice = (message: string) => {
    setNotice(message);
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), NOTICE_AUTO_DISMISS_MS);
  };

  // 草稿恢复 / 智能填写回填后重算高度：onChange 只覆盖手动输入，
  // 程序化改值不会触发 change，必须在此统一撑开，否则内容会被裁掉。
  useLayoutEffect(() => {
    fieldTextareasRef.current.forEach((textarea) => {
      if (textarea) autoGrowTextarea(textarea);
    });
  }, [businessFields, creativePrompt]);

  // 草稿自动落盘。
  useEffect(() => {
    if (!draftScope) return;
    savePodSemiDraft(draftScope.accountId, draftScope.workspaceId, {
      version: 1,
      business_fields: businessFields,
      creative_prompt: creativePrompt,
      count,
      brief_history: briefHistory,
    });
  }, [briefHistory, businessFields, count, creativePrompt, draftScope]);

  const loadBatches = async () => {
    setBatchesLoading(true);
    try {
      const response = await podSemiCustomizationApi.listBatches(20, 0);
      const next = response.batches.map((summary) => summary as unknown as SemiBatch);
      setBatches(next);
      // 列表按创建时间倒序，第一条就是最近一批，交给调用方决定是否展开。
      return next[0]?.id;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return undefined;
    } finally {
      setBatchesLoading(false);
    }
  };

  /** 批次请求目标守卫：切换批次后旧响应晚到会被丢弃，防止界面闪回旧批次。 */
  const batchRequestRef = useRef<string>("");

  const openBatch = async (batchId: string) => {
    setHistoryOpen(false);
    batchRequestRef.current = batchId;
    try {
      const batch = await podSemiCustomizationApi.getBatch(batchId);
      if (batchRequestRef.current !== batchId) return;
      setActiveBatch(batch);
    } catch (cause) {
      if (batchRequestRef.current !== batchId) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const refreshActiveBatch = async () => {
    if (!activeBatch) return;
    const batchId = activeBatch.id;
    try {
      const batch = await podSemiCustomizationApi.getBatch(batchId);
      if (batchRequestRef.current !== batchId) return;
      setActiveBatch(batch);
    } catch (cause) {
      if (batchRequestRef.current !== batchId) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  // 进页面直接展开最近一批，避免每次进来都是一片空白。
  useEffect(() => {
    void loadBatches().then((latestBatchId) => {
      if (latestBatchId) void openBatch(latestBatchId);
    });
  }, []);

  // 运行中批次轮询。
  useEffect(() => {
    if (!activeBatch || !isSemiBatchRunning(activeBatch.status)) return;
    const timer = window.setInterval(() => void refreshActiveBatch(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeBatch?.status, activeBatch?.id]);

  const onBriefGenerated = (fields: ReturnType<typeof briefFieldsToDraft>, input: string) => {
    setBusinessFields((current) => mergeBusinessFields(current, fields));
    setBriefHistory((current) => recordBriefHistory(current, createBriefHistoryItem(input, fields)));
    showNotice("已按描述自动填写业务字段，可继续在下方人工修改。");
  };

  const onSelectHistory = (item: PodBriefHistoryItem) => {
    setBusinessFields((current) => mergeBusinessFields(current, item.fields));
  };

  const missingRequired = SEMI_FIELDS.filter((field) => field.required && !businessFields[field.key].trim());

  const startBatch = async () => {
    if (busy) return;
    if (!isSemiCountValid(count)) {
      setError("半定制数量必须是 4 的倍数（4–200）。");
      return;
    }
    if (missingRequired.length > 0) {
      setError(`请填写必填字段：${missingRequired.map((field) => field.label).join("、")}。`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await podSemiCustomizationApi.createBatch({
        count,
        prompt_version: "v1",
        business_fields: semiBusinessFieldsForApi(businessFields),
        creative_prompt: creativePrompt.trim(),
      });
      setActiveBatch(created);
      showNotice(`已发起半定制批次：${count} 款图案 / ${semiGroupCount(count)} 次生图`);
      void loadBatches();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const control = async (action: "pause" | "cancel" | "resume" | "delete") => {
    if (!activeBatch || busy) return;
    setBusy(true);
    setError("");
    try {
      if (action === "pause") await podSemiCustomizationApi.pauseBatch(activeBatch.id);
      if (action === "cancel") await podSemiCustomizationApi.cancelBatch(activeBatch.id);
      if (action === "resume") await podSemiCustomizationApi.resumeBatch(activeBatch.id);
      if (action === "delete") {
        await podSemiCustomizationApi.deleteBatch(activeBatch.id);
        setActiveBatch(null);
        void loadBatches();
        setBusy(false);
        return;
      }
      await refreshActiveBatch();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  /**
   * 半定制一组四格 = 一组四款，失败只能按「整组」重跑（后端 regenerate_style 也是
   * 整组粒度）。所以这里按款反推失败组号，再逐组提交重试。
   */
  const failedStyleIndexes = useMemo(() => {
    if (!activeBatch) return [];
    return Array.from(
      new Set(
        activeBatch.items
          .filter((item) => item.status === "failed")
          .map((item) => item.style_index)
          .filter((value): value is number => typeof value === "number"),
      ),
    ).sort((left, right) => left - right);
  }, [activeBatch]);

  const retryFailedGroups = async () => {
    if (!activeBatch || busy || failedStyleIndexes.length === 0) return;
    setBusy(true);
    setError("");
    try {
      for (const styleIndex of failedStyleIndexes) {
        await podSemiCustomizationApi.regenerateGroup(activeBatch.id, styleIndex);
      }
      showNotice(`已提交 ${failedStyleIndexes.length} 组重试，每组重跑 4 款。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      // 失败也要对账一次：请求可能已经到达服务端并真的开始重跑（例如响应丢失、
      // 会话提示 401 但同时已提交），只弹错误会把界面留在过期的「失败」上。
      await refreshActiveBatch();
      void loadBatches();
      setBusy(false);
    }
  };

  const downloadZip = async () => {
    if (!activeBatch || busy) return;
    setBusy(true);
    setError("");
    try {
      const { filename, blob } = await podSemiCustomizationApi.downloadZip(activeBatch.id);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const downloadable = activeBatch
    && isSemiBatchTerminal(activeBatch.status)
    && activeBatch.completed_item_count > 0;

  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);

  // 批次被换掉或重跑后旧款可能已不存在，关掉大图层避免停在悬空数据上。
  useEffect(() => {
    if (selectedItemId && !activeBatch?.items.some((item) => item.id === selectedItemId)) setSelectedItemId(undefined);
  }, [activeBatch?.id, activeBatch?.items, selectedItemId]);

  const downloadPattern = async (path: string, filename: string) => {
    setBusy(true);
    setError("");
    try {
      downloadBlobAs(await blobForPath(path), filename);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pod-semi-customization-page">
      {/* 与全定制页同一套次级顶栏（.pod-page-header）：图标 + 眉标 + 标题。
          半定制不接模板、无导出记录，右侧不放操作按钮。 */}
      <header className="pod-page-header">
        <div className="pod-page-title">
          <span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" />
          <div>
            <span>POD SEMI · 纯图案</span>
            <h1>POD 半定制</h1>
          </div>
        </div>
        <div className="pod-page-header-actions">
          <button type="button" onClick={() => { setHistoryOpen(true); void loadBatches(); }}>
            <span className="iconfont icon-time-circle" />查看定制记录历史
          </button>
        </div>
      </header>

      {error && <p className="pod-semi-error" role="alert">{error}</p>}
      {notice && <p className="pod-semi-notice" role="status">{notice}</p>}

      <div className="pod-semi-grid">
        <aside className="pod-semi-setup">
          <PodBriefInput
            onGenerated={onBriefGenerated}
            history={briefHistory}
            onSelectHistory={onSelectHistory}
            subtitle="写「图案主题 + 风格」，AI 自动填好下方图案字段"
            hint="请写清图案主题与风格，例如：法式田园碎花，向日葵与藤蔓，奶油白配鼠尾草绿"
          />

          <section className="pod-semi-fields" aria-label="图案字段">
            {SEMI_FIELDS.map((field, fieldIndex) => (
              <label key={field.key} className="pod-semi-field">
                <span>
                  {field.label}
                  {field.required && <em>*</em>}
                  {field.hint && <i className="pod-field-info" data-tip={field.hint} aria-hidden="true">ⓘ</i>}
                </span>
                <textarea
                  rows={1}
                  ref={(element) => { fieldTextareasRef.current[fieldIndex] = element; }}
                  value={businessFields[field.key]}
                  onChange={(event) => {
                    setBusinessFields((current) => ({ ...current, [field.key]: event.currentTarget.value }));
                    autoGrowTextarea(event.currentTarget);
                  }}
                />
              </label>
            ))}
            <label className="pod-semi-field">
              <span>创意提示<small>选填</small></span>
              <textarea
                rows={1}
                ref={(element) => { fieldTextareasRef.current[SEMI_FIELDS.length] = element; }}
                value={creativePrompt}
                onChange={(event) => {
                  setCreativePrompt(event.currentTarget.value);
                  autoGrowTextarea(event.currentTarget);
                }}
              />
            </label>
          </section>

          <section className="pod-semi-count" aria-label="生成数量">
            <span>生成数量（图案款数）</span>
            <div className="pod-semi-count-options">
              {SEMI_BATCH_COUNTS.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={count === option ? "is-active" : ""}
                  onClick={() => setCount(option)}
                >{option}</button>
              ))}
            </div>
          </section>

          <button type="button" className="pod-semi-start" disabled={busy} onClick={() => void startBatch()}>
            {busy ? "提交中…" : "开始生成"}
          </button>
        </aside>

        <main className="pod-semi-results">
          {!activeBatch && (
            <p className="pod-semi-empty">暂无批次，发起第一个半定制批次后在此查看。</p>
          )}

          {activeBatch && (
            <section className="pod-semi-batch" aria-label="当前批次">
              <header className="pod-semi-batch-head">
                <div>
                  <b>{activeBatch.title || activeBatch.id}</b>
                  <small>{semiBatchStatusLabel(activeBatch.status)} · 已出 {activeBatch.completed_item_count}/{activeBatch.item_count} 款 · 失败 {activeBatch.failed_item_count}</small>
                </div>
                <div className="pod-semi-batch-actions">
                  {isSemiBatchRunning(activeBatch.status) && <button type="button" disabled={busy} onClick={() => void control("pause")}>暂停</button>}
                  {isSemiBatchPaused(activeBatch.status) && <button type="button" disabled={busy} onClick={() => void control("resume")}>继续</button>}
                  {(isSemiBatchRunning(activeBatch.status) || isSemiBatchPaused(activeBatch.status)) && <button type="button" disabled={busy} onClick={() => void control("cancel")}>取消</button>}
                  {failedStyleIndexes.length > 0 && !isSemiBatchRunning(activeBatch.status) && !isSemiBatchPaused(activeBatch.status) && (
                    <button type="button" disabled={busy} onClick={() => void retryFailedGroups()}>重试失败款</button>
                  )}
                  {isSemiBatchTerminal(activeBatch.status) && <button type="button" disabled={busy} onClick={() => void control("delete")}>删除</button>}
                  {downloadable && <button type="button" className="pod-semi-download" disabled={busy} onClick={() => void downloadZip()}>下载 ZIP</button>}
                </div>
              </header>

              <div className="pod-semi-items">
                {activeBatch.items.map((item) => {
                  const preview = item.pattern_preview_url;
                  return (
                    <figure key={item.id} className={`pod-semi-item status-${item.status}`}>
                      <button
                        type="button"
                        className="pod-semi-item-media"
                        disabled={!preview}
                        onClick={() => preview && setSelectedItemId(item.id)}
                        aria-label={`${semiItemTag(item.index)}，${semiItemStatusLabel(item.status)}${preview ? "，查看大图" : ""}`}
                      >
                        {preview
                          ? <PodAssetImage path={preview} alt={semiItemTag(item.index)} loading="lazy" decoding="async" />
                          : <span className="pod-semi-item-placeholder">{semiItemStatusLabel(item.status)}</span>}
                      </button>
                      <figcaption>
                        {semiItemTag(item.index)} · {semiItemStatusLabel(item.status)}
                      </figcaption>
                      {item.error_message && <i title={item.error_message}>!</i>}
                    </figure>
                  );
                })}
              </div>
            </section>
          )}
        </main>
      </div>

      <PodSemiResultLightbox
        batch={activeBatch}
        item={selectedItem}
        busy={busy}
        onClose={() => setSelectedItemId(undefined)}
        onDownload={downloadPattern}
      />

      <PodSemiBatchDrawer
        open={historyOpen}
        batches={batches}
        activeBatchId={activeBatch?.id}
        loading={batchesLoading}
        onOpen={(batchId) => void openBatch(batchId)}
        onRefresh={() => void loadBatches()}
        onClose={() => setHistoryOpen(false)}
      />
    </div>
  );
}
