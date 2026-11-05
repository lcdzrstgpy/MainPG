import { useEffect, useMemo, useRef, useState } from "react";

import {
  AD_ITEMS,
  dismissAd,
  readDismissedAdIds,
  reportAdEvent,
  type AdSlotId,
  type AdTarget,
} from "../ads/adConfig";

type AdSlotProps = {
  slot: AdSlotId;
  placement: "sidebar" | "footer";
  onOpenTarget: (target: AdTarget) => void;
};

/**
 * 工作台推广位：内容来自 shared/ads/adConfig.ts，关闭状态记在本机。
 * 同一位置配置了多条时，关掉一条就轮到下一条，全部关掉后整块不再渲染。
 */
export function AdSlot({ slot, placement, onOpenTarget }: AdSlotProps) {
  const [dismissed, setDismissed] = useState<string[]>(readDismissedAdIds);
  const item = useMemo(
    () => AD_ITEMS[slot].find((candidate) => !dismissed.includes(candidate.id)) ?? null,
    [slot, dismissed],
  );
  const rootRef = useRef<HTMLDivElement>(null);

  // 曝光只在真正进入视口时记一次：页脚推广位首屏通常看不到，加载即计数没有意义。
  useEffect(() => {
    const node = rootRef.current;
    if (!node || !item) return;
    let reported = false;
    const report = () => {
      if (reported) return;
      reported = true;
      reportAdEvent("impression", slot, item.id);
    };
    if (typeof IntersectionObserver === "undefined") {
      report();
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        report();
        observer.disconnect();
      }
    }, { threshold: 0.5 });
    observer.observe(node);
    return () => observer.disconnect();
  }, [slot, item?.id]);

  if (!item) return null;

  const handleOpen = () => {
    if (item.kind === "code") return;
    reportAdEvent("click", slot, item.id);
    onOpenTarget(item.target);
  };

  const className = [
    "ad-slot",
    `ad-slot--${placement}`,
    item.kind === "code" ? "ad-slot--code" : "",
    item.kind !== "code" && item.imageUrl ? "has-image" : "",
  ].filter(Boolean).join(" ");

  return (
    <div
      className={className}
      ref={rootRef}
      role="complementary"
      aria-label={`${item.title ?? "推广"}（${item.tag ?? "推广"}）`}
    >
      <button type="button" className="ad-slot-dismiss" onClick={() => setDismissed(dismissAd(item.id))} aria-label="关闭推广">
        ×
      </button>
      <span className="ad-slot-tag">{item.tag ?? "推广"}</span>

      {item.kind === "code" ? (
        <AdCodeMount html={item.customHtml} />
      ) : (
        <>
          {item.imageUrl && (
            <button type="button" className="ad-slot-image-button" onClick={handleOpen} aria-label={item.imageAlt ?? item.title}>
              <img
                className="ad-slot-image"
                src={item.imageUrl}
                alt={item.imageAlt ?? item.title}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            </button>
          )}
          <div className="ad-slot-copy">
            <strong className="ad-slot-title">{item.title}</strong>
            {item.description && <p className="ad-slot-desc">{item.description}</p>}
          </div>
          <button type="button" className="ad-slot-cta" onClick={handleOpen}>
            {item.ctaLabel}
            <span aria-hidden="true">→</span>
          </button>
        </>
      )}
    </div>
  );
}

/**
 * 第三方广告代码位。
 *
 * 联盟后台给的代码通常是一段 `<ins>` 加 `<script>`；React 用 innerHTML 插入不会执行脚本，
 * 所以这里先解析代码，再把每个 `<script>` 重新创建后插入，顺序保持与原始代码一致
 * （广告容器先出现、脚本后执行）。
 *
 * customHtml 会原样执行，只允许填广告平台后台生成的代码，不要拼接用户输入。
 */
function AdCodeMount({ html }: { html: string }) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.innerHTML = "";

    const template = document.createElement("template");
    template.innerHTML = html;
    Array.from(template.content.childNodes).forEach((node) => {
      if (node.nodeName === "SCRIPT") {
        const source = node as HTMLScriptElement;
        const script = document.createElement("script");
        for (const attribute of Array.from(source.attributes)) {
          script.setAttribute(attribute.name, attribute.value);
        }
        script.text = source.text;
        host.appendChild(script);
        return;
      }
      host.appendChild(node.cloneNode(true));
    });

    return () => {
      host.innerHTML = "";
    };
  }, [html]);

  return <div className="ad-slot-code" ref={hostRef} />;
}
