import type { WorkspaceModule } from "../../app/navigation/modules";

/**
 * 兜底页：模块没在 `workspacePageModules` 里登记（或 role 过滤后不可见）时落到这里。
 *
 * `module` 可以是 undefined —— 以前这个组件靠调用方 `!` 断言强转，注册表少一项就
 * 在 `module.iconClass` 上抛 TypeError，整棵 React 树跟着卸载成白屏。现在退化成
 * 一张「还没配好」的说明卡，至少用户知道点到了哪里、也能继续用别的功能。
 */
export function EmptyModulePage({
  module,
  moduleId,
}: {
  module?: WorkspaceModule;
  moduleId?: string;
}) {
  if (!module) {
    return <div className="module-placeholder">
      <section className="module-empty-card">
        <span className="empty-orbit" />
        <div>
          <h1>{moduleId || "未知模块"}</h1>
          <p>这个页面还没在导航注册表里登记，换一个入口试试。</p>
        </div>
      </section>
    </div>;
  }
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
