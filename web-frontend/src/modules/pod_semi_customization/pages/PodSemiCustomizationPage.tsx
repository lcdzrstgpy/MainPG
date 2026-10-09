import { useEffect, useMemo, useRef, useState } from "react";

import { getAuthAccount } from "../../../transport/http/client";
import { PodBriefInput } from "../../pod_customization/components/PodBriefInput";
import {
  briefFieldsToDraft,
  createBriefHistoryItem,
  isBriefRequestValid,
  mergeBusinessFields,
  normalizeBriefInput,
  recordBriefHistory,
} from "../../pod_customization/data/podBrief";
import { podCustomizationApi } from "../../pod_customization/api/podCustomizationApi";
import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import { formatPodBatchWaitingTime } from "../../pod_customization/data/podCustomizationModel";
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
  semiItemStatusLabel,
  semiItemTag,
} from "../data/podSemiCustomizationModel";
import {
  createEmptyPodSemiDraft,
  loadPodSemiDraft,
  savePodSemiDraft,
} from "../data/podSemiCustomizationDraft";
import type { SemiBatch } from "../types";
// 本页复用了全定制的「智能填写」组件（.pod-brief-input 等样式定义在该文件里）；
// 页面是按路由懒加载的，不显式引入的话单独打开本页会拿不到这些样式。
import "../../pod_customization/styles/podCustomization.css";
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
 * 半定制是纯图案花色制作：主题风格 / 元素关键词 / 配色 / 禁用元素全部由「智能填写」自动生成，
 * 用户不会手改，因此不在表单里展示（数据仍保存在 businessFields 中，照常进入 Prompt 与提交载荷）；
 * 产品名、品类、市场、人群、卖点对纯图案生成没有意义。后端 SemiBatchCreate 不校验这些字段。
 */

/** 交付图案的推进百分比：(已完成 + 已失败) / 总款数。 */
function semiProgressPercent(batch: SemiBatch): number {
  if (batch.item_count <= 0) return 0;
  return Math.min(100, Math.round(((batch.completed_item_count + batch.failed_item_count) / batch.item_count) * 100));
}

/** 批次标题：用 AI 定下的「整批统一风格」命名，历史列表里一眼能认出；
    风格缺失时退回元素关键词，再退回用户那句话。上限与后端 title 一致（120）。 */
function semiBatchTitle(fields: PodBusinessFieldsDraft, briefInput: string): string {
  const theme = fields.design_theme.trim();
  if (theme) return theme.slice(0, 120);
  const keywords = fields.style_keywords.trim();
  if (keywords) return keywords.slice(0, 120);
  return briefInput.slice(0, 120);
}

/** 「已等待」独立计时：运行中每秒只重渲染这一小段，终态按 updated_at 定格，数字不再跳。 */
function SemiWaitingTime({ createdAt, updatedAt, live }: { createdAt: string; updatedAt: string; live: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  const frozen = Date.parse(updatedAt);
  const end = live ? now : Number.isFinite(frozen) ? frozen : now;
  return <>已等待 {formatPodBatchWaitingTime(createdAt, end)} · </>;
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
  // 一句话需求：由「开始生成」一个按钮驱动，先让豆包编辑提示词，再直接发起生图。
  const [brief, setBrief] = useState("");
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
  // 批次记录抽屉里的勾选删除（只对已结束的批次开放）。
  const [selectedBatchIds, setSelectedBatchIds] = useState<string[]>([]);
  const [deletingSelection, setDeletingSelection] = useState(false);

  const noticeTimerRef = useRef<number | undefined>(undefined);

  const showNotice = (message: string) => {
    setNotice(message);
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), NOTICE_AUTO_DISMISS_MS);
  };

  // 草稿自动落盘。
  useEffect(() => {
    if (!draftScope) return;
    savePodSemiDraft(draftScope.accountId, draftScope.workspaceId, {
      version: 1,
      business_fields: businessFields,
      creative_prompt: "",
      count,
      brief_history: briefHistory,
    });
  }, [briefHistory, businessFields, count, draftScope]);

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

  const onSelectHistory = (item: PodBriefHistoryItem) => {
    // 历史项就是历史「一句话需求」：回填输入框，点开始生成会按它重新编辑并生图。
    setBrief(item.input);
    setBusinessFields((current) => mergeBusinessFields(current, item.fields));
  };

  // 一个按钮走完整条链路：豆包先把一句话整理成图案提示词，再用它直接发起生图。
  const startBatch = async () => {
    if (busy) return;
    if (!isSemiCountValid(count)) {
      setError("半定制数量必须是 4 的倍数（4–200）。");
      return;
    }
    if (!isBriefRequestValid(brief)) {
      setError("请先写下图案需求（1–500 字），例如：法式田园碎花，向日葵与藤蔓，奶油白配鼠尾草绿。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const input = normalizeBriefInput(brief);
      const response = await podCustomizationApi.generateBriefFields({ brief: input, locale: "zh-CN" });
      // briefFieldsToDraft 不含 copy_restrictions 等字段，用 mergeBusinessFields 补齐成完整草稿。
      const fields = mergeBusinessFields(businessFields, briefFieldsToDraft(response.fields));
      setBusinessFields(fields);
      setBriefHistory((current) => recordBriefHistory(current, createBriefHistoryItem(input, fields)));
      const created = await podSemiCustomizationApi.createBatch({
        count,
        prompt_version: "v1",
        business_fields: semiBusinessFieldsForApi(fields),
        creative_prompt: "",
        title: semiBatchTitle(fields, input),
      });
      // 必须同步轮询目标：refreshActiveBatch 会用 batchRequestRef 做守卫，
      // 不更新的话新批次一直在"排队中"（轮询结果全被丢弃）。
      batchRequestRef.current = created.id;
      setActiveBatch(created);
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

  const toggleSelectBatch = (batchId: string) => {
    setSelectedBatchIds((current) => current.includes(batchId)
      ? current.filter((id) => id !== batchId)
      : [...current, batchId]);
  };

  /** 批量删除：只处理已结束的批次（抽屉里进行中的批次勾选框本来就是禁用的，这里再兜一次底）。 */
  const deleteSelectedBatches = async () => {
    const ids = selectedBatchIds.filter((id) => batches.some((batch) => batch.id === id && isSemiBatchTerminal(batch.status)));
    if (!ids.length || busy || deletingSelection) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 个批次？删除后这些批次的本地图片将被清理，不可恢复。`)) return;
    setDeletingSelection(true);
    setError("");
    const deleted: string[] = [];
    let failed = false;
    try {
      for (const batchId of ids) {
        await podSemiCustomizationApi.deleteBatch(batchId);
        deleted.push(batchId);
      }
    } catch (cause) {
      failed = true;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBatches((current) => current.filter((batch) => !deleted.includes(batch.id)));
      if (activeBatch && deleted.includes(activeBatch.id)) {
        setActiveBatch(null);
        setSelectedItemId(undefined);
      }
      setSelectedBatchIds((current) => current.filter((id) => !deleted.includes(id)));
      if (!failed && deleted.length) showNotice(`${deleted.length} 个批次已删除。`);
      setDeletingSelection(false);
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

  const semiProgress = activeBatch ? semiProgressPercent(activeBatch) : 0;

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
            value={brief}
            onValueChange={setBrief}
            hideGenerate
            history={briefHistory}
            onSelectHistory={onSelectHistory}
            subtitle="写「图案主题 + 风格」，点「开始生成」后由豆包整理成提示词并直接生图"
            hint="请写清图案主题与风格，例如：法式田园碎花，向日葵与藤蔓，奶油白配鼠尾草绿"
          />

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
            {busy ? "正在编辑提示词并生图…" : `开始生成 ${count} 款图案`}
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
                  <small>{semiBatchStatusLabel(activeBatch.status)}</small>
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

              <div className="pod-semi-progress" role="progressbar" aria-label="半定制批次进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={semiProgress}>
                <div><span style={{ width: `${semiProgress}%` }} /></div>
                <small><SemiWaitingTime createdAt={activeBatch.created_at} updatedAt={activeBatch.updated_at} live={isSemiBatchRunning(activeBatch.status)} />完成 {activeBatch.completed_item_count}/{activeBatch.item_count} 款 · 失败 {activeBatch.failed_item_count} 款 · {semiProgress}%</small>
              </div>

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
        busy={busy}
        deleting={deletingSelection}
        selectedIds={selectedBatchIds}
        onToggleSelect={toggleSelectBatch}
        onDeleteSelected={() => void deleteSelectedBatches()}
        onOpen={(batchId) => void openBatch(batchId)}
        onRefresh={() => void loadBatches()}
        onClose={() => setHistoryOpen(false)}
      />
    </div>
  );
}
