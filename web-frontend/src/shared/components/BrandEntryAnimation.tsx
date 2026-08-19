import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { BRAND_LOGO_URL, BRAND_NAME } from "../brand";

type BrandEntryAnimationProps = {
  active: boolean;
  onComplete: () => void;
};

type EntryGeometry = {
  endLeft: number;
  endTop: number;
  endWidth: number;
  startWidth: number;
};

type EntryStyle = CSSProperties & {
  "--brand-entry-end-left": string;
  "--brand-entry-end-top": string;
  "--brand-entry-end-width": string;
  "--brand-entry-start-width": string;
};

const ENTRY_DURATION_MS = 2300;

function elementIsVisible(element: HTMLElement | null) {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  return rect.width > 1 && rect.height > 1 && style.display !== "none" && style.visibility !== "hidden";
}

export function BrandEntryAnimation({ active, onComplete }: BrandEntryAnimationProps) {
  const [geometry, setGeometry] = useState<EntryGeometry | null>(null);
  const completedRef = useRef(false);

  useLayoutEffect(() => {
    if (!active) return;

    completedRef.current = false;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const frame = window.requestAnimationFrame(onComplete);
      return () => window.cancelAnimationFrame(frame);
    }

    const brandTarget = document.querySelector<HTMLElement>("[data-brand-entry-target]");
    const fallbackTarget = document.querySelector<HTMLElement>(".sidebar-pin-button");
    const target = elementIsVisible(brandTarget) ? brandTarget : fallbackTarget;
    const rect = target?.getBoundingClientRect();
    const targetIsBrand = target === brandTarget;

    setGeometry({
      endLeft: rect ? rect.left + rect.width / 2 : 42,
      endTop: rect ? rect.top + rect.height / 2 : 38,
      endWidth: rect ? (targetIsBrand ? rect.width : Math.min(rect.width, 42)) : 42,
      startWidth: Math.min(480, Math.max(260, window.innerWidth * 0.46)),
    });

    const timer = window.setTimeout(onComplete, ENTRY_DURATION_MS + 240);
    return () => window.clearTimeout(timer);
  }, [active, onComplete]);

  if (!active || !geometry) return null;

  const style: EntryStyle = {
    "--brand-entry-end-left": `${geometry.endLeft}px`,
    "--brand-entry-end-top": `${geometry.endTop}px`,
    "--brand-entry-end-width": `${geometry.endWidth}px`,
    "--brand-entry-start-width": `${geometry.startWidth}px`,
  };

  const finish = () => {
    if (completedRef.current) return;
    completedRef.current = true;
    onComplete();
  };

  return (
    <div className="workspace-entry-overlay" aria-hidden="true">
      <div
        className="workspace-entry-brand"
        style={style}
        onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish();
        }}
      >
        <span className="workspace-entry-halo" />
        <img src={BRAND_LOGO_URL} alt={BRAND_NAME} />
      </div>
    </div>
  );
}
