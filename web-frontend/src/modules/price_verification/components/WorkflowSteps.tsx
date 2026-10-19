import { useLayoutEffect, useRef, useState } from "react";

import { priceVerificationStages, type PriceVerificationStage } from "../types";

type Props = {
  activeStage: PriceVerificationStage;
  canOpen: (stage: PriceVerificationStage) => boolean;
  onOpen: (stage: PriceVerificationStage) => void;
};

export function WorkflowSteps({ activeStage, canOpen, onOpen }: Props) {
  const groupRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState({ left: 0, width: 0 });
  const [ready, setReady] = useState(false);

  // 选中高亮是同一块滑块在移动，而不是三块卡片各自换底色。
  useLayoutEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    const sync = () => {
      const active = group.querySelector<HTMLElement>(".price-verification-step.is-current-flow");
      if (!active) return;
      const g = group.getBoundingClientRect();
      const a = active.getBoundingClientRect();
      setThumb({ left: a.left - g.left - group.clientLeft, width: a.width });
    };
    sync();
    const frame = requestAnimationFrame(() => setReady(true));
    const observer = new ResizeObserver(sync);
    observer.observe(group);
    group.querySelectorAll<HTMLElement>(".price-verification-step").forEach((step) => observer.observe(step));
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [activeStage]);

  return <div
    ref={groupRef}
    className="price-verification-steps"
    data-ready={ready ? "true" : "false"}
    aria-label="核价工作流"
  >
    <span
      className="price-verification-step-thumb"
      style={{ transform: `translateX(${thumb.left}px)`, width: thumb.width }}
      aria-hidden="true"
    />
    {priceVerificationStages.map((stage, index) => {
      const available = canOpen(stage.id);
      const active = activeStage === stage.id;
      return <div
        key={stage.id}
        role="button"
        tabIndex={available ? 0 : -1}
        className={`price-verification-step${active ? " is-current-flow" : ""}`}
        onClick={() => { if (available) onOpen(stage.id); }}
        onKeyDown={(event) => {
          if (!available || (event.key !== "Enter" && event.key !== " ")) return;
          event.preventDefault();
          onOpen(stage.id);
        }}
        aria-disabled={!available}
        aria-current={active ? "step" : undefined}
      >
        <span className="price-verification-step-number">{stage.number}</span><span><strong>{stage.title}</strong><small>{stage.description}</small></span>{index < priceVerificationStages.length - 1 && <b aria-hidden="true">→</b>}
      </div>;
    })}
  </div>;
}
