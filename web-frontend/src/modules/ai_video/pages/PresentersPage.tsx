import { PresenterManager } from "../components/PresenterManager";

type PresentersPageProps = {
  onNavigate: (path: string) => void;
};

/** 主播素材：角色库的一级入口（与设置页角色区共用同一个管理器）。 */
export function PresentersPage({ onNavigate }: PresentersPageProps) {
  return (
    <section className="ai-video-page" aria-label="主播素材">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>主播素材</h2>
          <p>管理可复用的出镜角色，生成四视图定妆照，保证跨镜头的人物一致性。</p>
        </div>
        <button type="button" className="ai-video-header-action" onClick={() => onNavigate("/start")}>
          去创建视频
        </button>
      </header>
      <PresenterManager />
    </section>
  );
}
