import { priceVerificationStages, type PriceVerificationStage } from "../types";

type Props = {
  activeStage: PriceVerificationStage;
  canOpen: (stage: PriceVerificationStage) => boolean;
  onOpen: (stage: PriceVerificationStage) => void;
};

export function WorkflowSteps({ activeStage, canOpen, onOpen }: Props) {
  return <div className="price-verification-steps" aria-label="核价工作流">
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
