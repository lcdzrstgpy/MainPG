import { priceVerificationStages, type PriceVerificationStage } from "../types";

type Props = { activeStage: PriceVerificationStage };

/** 工作流步骤条：纯只读展示，无激活/悬停动效。 */
export function WorkflowSteps({ activeStage }: Props) {
  return <div className="price-verification-steps" aria-label="核价工作流">
    {priceVerificationStages.map((stage) => <div key={stage.id} className="price-verification-step">
      <span className="price-verification-step-number">{stage.number}</span><span><strong>{stage.title}</strong><small>{stage.description}</small></span><b aria-hidden="true">→</b>
    </div>)}
  </div>;
}
