import '../styles/ProductFlowSteps.css';

/** 工作流步骤数据：由各页按自己的流程传入（如组合套装工作流）。 */
export type ProductFlowStep = {
  id: string;
  number: string;
  title: string;
  description: string;
};

type Props = {
  steps: ProductFlowStep[];
  activeId: string;
  canOpen: (id: string) => boolean;
  onOpen: (id: string) => void;
  label: string;
};

/** 产品处理工作流步骤条：展示「草稿池 → 处理设置 → 结果预检」的流转位置，并支持点击切页。 */
export function ProductFlowSteps({ steps, activeId, canOpen, onOpen, label }: Props) {
  return (
    <div className="product-flow-steps" aria-label={label}>
      {steps.map((step, index) => {
        const available = canOpen(step.id);
        const active = activeId === step.id;
        return (
          <div
            key={step.id}
            role="button"
            tabIndex={available ? 0 : -1}
            className={`product-flow-step${active ? ' is-current-flow' : ''}`}
            onClick={() => { if (available) onOpen(step.id); }}
            onKeyDown={(event) => {
              if (!available || (event.key !== 'Enter' && event.key !== ' ')) return;
              event.preventDefault();
              onOpen(step.id);
            }}
            aria-disabled={!available}
            aria-current={active ? 'step' : undefined}
          >
            <span className="product-flow-step-number">{step.number}</span>
            <span><strong>{step.title}</strong><small>{step.description}</small></span>
            {index < steps.length - 1 && <b aria-hidden="true">→</b>}
          </div>
        );
      })}
    </div>
  );
}
