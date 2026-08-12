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
      return <button
        key={stage.id}
        type="button"
        className={`price-verification-step${active ? " is-active" : ""}`}
        onClick={() => onOpen(stage.id)}
        disabled={!available}
        aria-current={active ? "step" : undefined}
      >
        <span className="price-verification-step-number">{stage.number}</span><span><strong>{stage.title}</strong><small>{stage.description}</small></span>{index < priceVerificationStages.length - 1 && <b aria-hidden="true">→</b>}
      </button>;
    })}
  </div>;
}
