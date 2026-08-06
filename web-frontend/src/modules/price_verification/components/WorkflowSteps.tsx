import { priceVerificationStages, type PriceVerificationStage } from "../types";

type Props = { activeStage: PriceVerificationStage; onSelect: (stage: PriceVerificationStage) => void };

export function WorkflowSteps({ activeStage, onSelect }: Props) {
  return <div className="price-verification-steps" aria-label="核价工作流">
    {priceVerificationStages.map((stage) => <button key={stage.id} className={`price-verification-step ${activeStage === stage.id ? "is-active" : ""}`} onClick={() => onSelect(stage.id)} aria-pressed={activeStage === stage.id}>
      <span className="price-verification-step-number">{stage.number}</span><span><strong>{stage.title}</strong><small>{stage.description}</small></span><b aria-hidden="true">→</b>
    </button>)}
  </div>;
}
