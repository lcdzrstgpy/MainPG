import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { podCustomizationApi } from "../api/podCustomizationApi";
import {
  POD_BRIEF_MAX_LENGTH,
  briefFieldsToDraft,
  isBriefRequestValid,
  normalizeBriefInput,
} from "../data/podBrief";
import type { PodBriefFieldsDraft, PodBriefHistoryItem } from "../types";

type Props = {
  onGenerated: (fields: PodBriefFieldsDraft, input: string) => void;
  history: PodBriefHistoryItem[];
  onSelectHistory: (item: PodBriefHistoryItem) => void;
};

// 阶段化文案：调用期间按固定节奏轮换，避免只显示一个静态的“生成中”。
const BRIEF_STAGES = ["正在理解需求…", "正在组织字段…"];
const BRIEF_STAGE_INTERVAL_MS = 1_800;
const BRIEF_SUMMARY_MAX_LENGTH = 40;
// 输入框随内容自适应高度；超过该高度后改为内部滚动，避免撑破侧栏。
const BRIEF_TEXTAREA_MAX_HEIGHT = 240;

function briefTimeLabel(createdAt: string): string {
  const parsed = Date.parse(createdAt);
  if (!Number.isFinite(parsed)) return createdAt;
  return new Date(parsed).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function briefSummary(input: string): string {
  const normalized = normalizeBriefInput(input);
  return normalized.length > BRIEF_SUMMARY_MAX_LENGTH ? `${normalized.slice(0, BRIEF_SUMMARY_MAX_LENGTH)}…` : normalized;
}

export function PodBriefInput({ onGenerated, history, onSelectHistory }: Props) {
  const [open, setOpen] = useState(true);
  const [brief, setBrief] = useState("");
  const [loading, setLoading] = useState(false);
  const [stageIndex, setStageIndex] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 输入框自适应高度：内容变多自动长高，清空或收起时收回；超过上限后内部滚动。
  const growTextarea = () => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, BRIEF_TEXTAREA_MAX_HEIGHT)}px`;
  };

  useLayoutEffect(() => {
    if (open) growTextarea();
  }, [open, brief]);

  useEffect(() => {
    if (!loading) {
      setStageIndex(0);
      return;
    }
    const timer = window.setInterval(() => {
      setStageIndex((current) => Math.min(current + 1, BRIEF_STAGES.length - 1));
    }, BRIEF_STAGE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loading]);

  // 异常一律在此收敛：后端不可用/返回结构异常时只展示可读错误，绝不让异常冒泡导致页面白屏。
  const generate = async () => {
    if (loading || !isBriefRequestValid(brief)) return;
    const input = normalizeBriefInput(brief);
    setError("");
    setNotice("");
    setLoading(true);
    try {
      const response = await podCustomizationApi.generateBriefFields({ brief: input, locale: "zh-CN" });
      onGenerated(briefFieldsToDraft(response.fields), input);
      setNotice("已按描述自动填写业务字段，可继续在下方人工修改。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause) || "智能生成失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="pod-brief-input" aria-label="智能填写">
      <button type="button" className="pod-brief-input-toggle" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
        <span><b>智能填写</b><small>写「产品名 + 风格」，AI 自动填好下方业务字段</small></span>
        <i className={`iconfont icon-down ${open ? "is-open" : ""}`} aria-hidden="true" />
      </button>
      {open && <div className="pod-brief-input-body">
        <textarea
          ref={textareaRef}
          value={brief}
          maxLength={POD_BRIEF_MAX_LENGTH}
          disabled={loading}
          aria-label="智能填写主题或需求"
          onChange={(event) => {
            setBrief(event.currentTarget.value);
            setError("");
            setNotice("");
          }}
        />
        <div className="pod-brief-input-meta">
          <span className={loading ? "pod-brief-input-stage" : ""}>{loading ? BRIEF_STAGES[stageIndex] : "请务必带上产品名，格式：产品名 + 风格（可再补目标人群、卖点）"}</span>
          <small>{brief.length}/{POD_BRIEF_MAX_LENGTH}</small>
        </div>
        <button type="button" className="pod-brief-input-generate" disabled={loading || !isBriefRequestValid(brief)} onClick={() => void generate()}>
          {loading ? <><span className="iconfont icon-loading" aria-hidden="true" />{BRIEF_STAGES[stageIndex]}</> : <><span className="iconfont icon-robot" aria-hidden="true" />智能生成</>}
        </button>
        {error && <p className="pod-brief-input-error" role="alert"><span>{error}</span><button type="button" onClick={() => void generate()}>重试</button></p>}
        {!error && notice && <p className="pod-brief-input-notice" role="status">{notice}</p>}
        {history.length > 0 && <div className="pod-brief-history" aria-label="最近生成">
          <b>最近生成</b>
          <ul>
            {history.map((item) => <li key={item.id}>
              <button type="button" onClick={() => onSelectHistory(item)}>
                <span>{briefSummary(item.input)}</span>
                <small>{briefTimeLabel(item.created_at)}</small>
              </button>
            </li>)}
          </ul>
        </div>}
      </div>}
    </section>
  );
}
