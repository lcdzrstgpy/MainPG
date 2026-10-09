import type { WorkspaceModule } from "../../app/navigation/modules";

export function EmptyModulePage({ module }: { module: WorkspaceModule }) {
  return <div className="module-placeholder">
    <section className="module-empty-card">
      <span className={`empty-orbit ${module.iconClass ?? ""}`}>{module.icon}</span>
      <div>
        <h1>{module.label}</h1>
        <p>{module.description}</p>
      </div>
    </section>
  </div>;
}
