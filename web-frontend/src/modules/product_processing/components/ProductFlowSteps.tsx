import '../styles/ProductFlowSteps.css';

export type ProductFlowStep = {
  id: string;
  number: string;
  title: string;
  description: string;
};

/** 产品处理三步流水线：草稿池 → 处理设置 → 结果预检。草稿池 / 处理设置 / 结果预检三页共用同一份文案。 */
export const PRODUCT_FLOW_STEPS: ProductFlowStep[] = [
  { id: 'pool', number: '01', title: '采集草稿池', description: '从「采集」或「每日选品」入池，管理批次与草稿' },
  { id: 'settings', number: '02', title: '处理设置', description: '勾选草稿后进入处理设置，配置 AI 处理范围' },
  { id: 'precheck', number: '03', title: '结果预检', description: '任务完成后查看结果、预检并导出最终版' },
];

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

type CardProps = {
  activeId: string;
  canOpen: (id: string) => boolean;
  onOpen: (id: string) => void;
};

/** 带标题的工作流卡片：让「草稿池」「处理设置」「结果预检」三页共用同一个外壳，避免页面之间视觉割裂。 */
export function ProductFlowCard({ activeId, canOpen, onOpen }: CardProps) {
  return (
    <section className="verify-flow-card">
      <div className="verify-flow-heading">
        <span aria-hidden="true"><i className="iconfont icon-build" /></span>
        <strong>产品处理工作流</strong>
        <small>草稿池 → 处理设置 → 结果预检，按顺序流转</small>
      </div>
      <ProductFlowSteps
        steps={PRODUCT_FLOW_STEPS}
        activeId={activeId}
        canOpen={canOpen}
        onOpen={onOpen}
        label="产品处理工作流"
      />
    </section>
  );
}
