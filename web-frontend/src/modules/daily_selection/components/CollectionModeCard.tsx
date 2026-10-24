import { useEffect, useState } from "react";

/** 轮播自动切换节奏：4.6 秒一张。 */
const AUTO_PLAY_MS = 4600;

export type CollectionModeSlide = { src: string };

type Props = {
  label: string;
  iconClass: string;
  slides: CollectionModeSlide[];
  isActive: boolean;
  onSelect: () => void;
};

/**
 * 采集入口卡片（标签 + 下方展示区），背景是截图/商品图轮播。
 *
 * 规则（2026-10-24，用户指定）：
 *  1. **只有选中的卡片才轮播**；未选中的冻结在当前帧，不启动定时器；
 *  2. 选中 → 背景清晰；未选中 → 背景模糊并压白纱；
 *  3. 「定向采集」这类文字放在独立胶囊里，任何图都读得清。
 *
 * 未选中的卡片图片仍按模糊样式渲染（CSS 负责），切回来时先清晰再开始轮播。
 */
export function CollectionModeCard({ label, iconClass, slides, isActive, onSelect }: Props) {
  const [index, setIndex] = useState(0);
  const count = slides.length;

  useEffect(() => {
    if (!isActive || count < 2) return;
    // 系统要求减少动态效果时不自动轮播（与主题动画的降级口径一致）。
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % count), AUTO_PLAY_MS);
    return () => window.clearInterval(timer);
  }, [isActive, count]);

  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      className={`collection-mode-card${isActive ? " is-active" : ""}`}
      onClick={onSelect}
    >
      <span className="collection-mode-media" aria-hidden="true">
        {slides.map((slide, i) => (
          <img
            key={slide.src}
            className={`collection-mode-img${i === index ? " is-active" : ""}`}
            src={slide.src}
            alt=""
            loading="lazy"
            draggable={false}
          />
        ))}
      </span>
      <span className="collection-mode-veil" aria-hidden="true" />
      <span className="collection-mode-label">
        <span className={`iconfont ${iconClass}`} aria-hidden="true" />
        {label}
      </span>
    </button>
  );
}
