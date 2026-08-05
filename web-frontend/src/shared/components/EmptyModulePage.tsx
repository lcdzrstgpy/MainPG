import type { WorkspaceModule } from "../../app/navigation/modules";

export function EmptyModulePage({ module }: { module: WorkspaceModule }) {
  return <div className="module-placeholder">
    <section className="module-empty-card">
      <span className="empty-orbit">{module.icon}</span>
      <div>
        <p className="eyebrow">MODULE / {module.id.toUpperCase()}</p>
        <h1>{module.label}</h1>
        <p>{module.description}</p>
        <p>后续成员可在 <code>src/modules/{module.id}</code> 中分别编写 API、页面、组件、状态和类型。</p>
      </div>
    </section>
  </div>;
}
