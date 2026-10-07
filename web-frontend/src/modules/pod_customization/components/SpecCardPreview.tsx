import { useEffect, useRef, useState } from "react";

import { podCustomizationApi } from "../api/podCustomizationApi";
import type { SpecCardCorner, SpecCardDisplayUnit, SpecCardStyle } from "../types";

type Props = {
  cells: string[][];
  style: SpecCardStyle;
  corner: SpecCardCorner;
  /** 是否把卡片印到素材图上；关掉时预览与生成结果一致，给干净底图。 */
  enabled: boolean;
  displayUnit: SpecCardDisplayUnit;
  /** 预览底图优先用该批次模板；缺省时后端自选白底图。 */
  baseTemplateId?: string;
};

/** 输入变化后 300ms 防抖刷新预览。 */
export const SPEC_CARD_PREVIEW_DEBOUNCE_MS = 300;
export const SPEC_CARD_PREVIEW_NOTE = "示意效果，最终以生成结果为准。";

export function SpecCardPreview({ cells, style, corner, enabled, displayUnit, baseTemplateId }: Props) {
  const [image, setImage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!cells.length) return;
    let cancelled = false;
    const generation = requestRef.current + 1;
    requestRef.current = generation;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setLoading(true);
      setError("");
      void podCustomizationApi.previewSpecCard({
        cells: cells.map((row) => [...row]),
        style,
        corner,
        display_unit: displayUnit,
        enabled,
        ...(baseTemplateId ? { base_template_id: baseTemplateId } : {}),
      }).then((response) => {
        if (cancelled || requestRef.current !== generation) return;
        setImage(response.image);
      }).catch((cause) => {
        if (cancelled || requestRef.current !== generation) return;
        // 预览失败不阻塞配置：保留上一次成功的示意图，只提示可重试。
        setError(cause instanceof Error ? cause.message : String(cause));
      }).finally(() => {
        if (cancelled || requestRef.current !== generation) return;
        setLoading(false);
      });
    }, SPEC_CARD_PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [cells, style, corner, enabled, displayUnit, baseTemplateId, reloadToken]);

  return (
    <section className="pod-spec-card-preview" aria-label="预览">
      <div className="pod-spec-card-section-title"><span>PREVIEW</span><h3>预览</h3></div>
      <div className="pod-spec-card-preview-frame">
        {image
          ? <img src={image} alt="规格卡示意图" />
          : <span className="pod-spec-card-preview-empty">正在生成示意图…</span>}
        {loading && <i className="pod-spec-card-preview-loading" role="status">预览刷新中…</i>}
      </div>
      <p className="pod-spec-card-preview-note">{SPEC_CARD_PREVIEW_NOTE}</p>
      {error && (
        <p className="pod-spec-card-preview-error" role="alert">
          <span>预览生成失败：{error}</span>
          <button type="button" onClick={() => setReloadToken((token) => token + 1)}>重试</button>
        </p>
      )}
    </section>
  );
}
