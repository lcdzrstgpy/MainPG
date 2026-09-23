import { useEffect, useMemo, useState } from 'react';

/** 轮播自动切换节奏：多图时每张停留 4 秒，与公告弹窗保持一致。 */
const AUTO_PLAY_INTERVAL = 4000;

export type ModeCardSlide = { src: string; caption: string };

type Props = { slides: ModeCardSlide[] };

/**
 * 选型卡内的广告位轮播：方形画框 + 淡入切换 + 角标与指示点，悬停暂停。
 * 选型卡本身是 role="radio"，所以指示点点击时 stopPropagation，避免误触发选型。
 * 图片加载失败会被剔除，不会留下破图。
 */
export function ModeCardCarousel({ slides }: Props) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [broken, setBroken] = useState<string[]>([]);

  const available = useMemo(() => slides.filter((s) => !broken.includes(s.src)), [slides, broken]);
  const current = available.length ? Math.min(index, available.length - 1) : 0;

  useEffect(() => {
    if (paused || available.length < 2) return;
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % available.length), AUTO_PLAY_INTERVAL);
    return () => window.clearInterval(timer);
  }, [paused, available.length]);

  if (!available.length) return null;

  return (
    <span
      className="combo-card-carousel"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {available.map((slide, i) => (
        <img
          key={slide.src}
          className={`combo-card-carousel-img${i === current ? ' is-active' : ''}`}
          src={slide.src}
          alt=""
          loading="lazy"
          draggable={false}
          onError={() => setBroken((prev) => (prev.includes(slide.src) ? prev : [...prev, slide.src]))}
        />
      ))}
      <span className="combo-card-carousel-footer">
        <span className="combo-card-carousel-caption">{available[current].caption}</span>
        {available.length > 1 && (
          <span className="combo-card-carousel-dots">
            {available.map((slide, i) => (
              <button
                key={slide.src}
                type="button"
                className={`combo-card-carousel-dot${i === current ? ' is-active' : ''}`}
                aria-label={`查看${slide.caption}`}
                onClick={(e) => { e.stopPropagation(); setIndex(i); }}
              />
            ))}
          </span>
        )}
      </span>
    </span>
  );
}
