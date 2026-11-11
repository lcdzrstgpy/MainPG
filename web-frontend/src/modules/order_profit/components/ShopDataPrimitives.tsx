import type { ReactNode } from "react";

import { ShopDataArt, type ShopDataArtVariant } from "./ShopDataArt";
import { ShopDataGlyph, type ShopDataGlyphName } from "./ShopDataGlyph";
import { ShopDataScene, type ShopDataSceneVariant } from "./ShopDataScene";

/**
 * 店铺数据页的统一分层容器：
 * - PageHero：板块定位（是什么、适合什么时候看）
 * - SectionCard：一块内容一个标题、一句说明，右侧放时间/数量，可选小场景
 * - EmptyBlock：没有数据时给插画 + 下一步动作，避免只剩一句冷冰冰的提示
 */

export function PageHero({
  eyebrow,
  title,
  desc,
  art,
  aside,
}: {
  eyebrow: string;
  title: string;
  desc: string;
  art: ShopDataArtVariant;
  aside?: ReactNode;
}) {
  return (
    <header className={`op-hero is-${art}`}>
      <div className="op-hero-copy">
        <span className="op-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{desc}</p>
        {aside ? <div className="op-hero-aside">{aside}</div> : null}
      </div>
      <div className="op-hero-art" aria-hidden="true">
        <ShopDataArt variant={art} />
      </div>
    </header>
  );
}

export function SectionCard({
  title,
  desc,
  meta,
  action,
  children,
  className = "",
  icon,
  scene,
}: {
  title: string;
  desc?: string;
  meta?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  icon?: ShopDataGlyphName;
  scene?: ShopDataSceneVariant;
}) {
  return (
    <section className={`op-card op-section ${className}`.trim()}>
      <header className={`op-section-head${icon || scene ? " has-visual" : ""}`}>
        {icon ? (
          <span className="op-section-glyph" aria-hidden="true">
            <ShopDataGlyph name={icon} />
          </span>
        ) : null}
        <div className="op-section-copy">
          <h2>{title}</h2>
          {desc ? <p>{desc}</p> : null}
        </div>
        {scene ? (
          <span className="op-section-scene" aria-hidden="true">
            <ShopDataScene variant={scene} />
          </span>
        ) : null}
        {meta || action ? (
          <div className="op-section-meta">
            {meta}
            {action}
          </div>
        ) : null}
      </header>
      <div className="op-section-body">{children}</div>
    </section>
  );
}

export function EmptyBlock({
  title,
  desc,
  art = "empty",
  action,
  compact = false,
}: {
  title: string;
  desc: string;
  art?: ShopDataArtVariant;
  action?: ReactNode;
  compact?: boolean;
}) {
  if (compact) {
    return (
      <div className="op-empty">
        <strong>{title}</strong>
        <span>{desc}</span>
        {action}
      </div>
    );
  }
  return (
    <div className="op-empty-block">
      <ShopDataArt variant={art} className="op-empty-art" seal={false} />
      <strong>{title}</strong>
      <span>{desc}</span>
      {action ? <div className="op-empty-action">{action}</div> : null}
    </div>
  );
}

/** 主指标带：只放用户进页面最该看到的 3-4 个数字。 */
export function MetricBand({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`op-metric-band ${className}`.trim()}>{children}</div>;
}

export function Metric({
  label,
  value,
  tone = "",
  hint,
  glyph,
}: {
  label: string;
  value: ReactNode;
  tone?: string;
  hint?: string;
  glyph?: ShopDataGlyphName;
}) {
  return (
    <div className={`op-metric ${tone === "is-negative" ? "is-loss" : ""}`.trim()}>
      <span>
        {glyph ? <ShopDataGlyph name={glyph} className="op-metric-glyph" /> : <i className="op-metric-dot" aria-hidden="true" />}
        {label}
      </span>
      <strong className={tone}>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}
