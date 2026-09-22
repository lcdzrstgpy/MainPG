import { useEffectPreferences } from "../../../shared/hooks/useEffectPreferences";
import { useSidebarPreferences } from "../../../shared/hooks/useSidebarState";
import { useTopbarCollapse } from "../../../shared/hooks/useTopbarCollapse";
import { BALL_SIZE_OPTIONS, useBallSize } from "../../../shared/hooks/useBallSize";

/**
 * 偏好设置面板：开关界面行为与动效。
 * 设置保存在本机（localStorage），重启后依然生效，随时可以改回来。
 */
export function PreferencesPanel() {
  const { tap, ambient, setEffects } = useEffectPreferences();
  const { collapsed: sidebarCollapsed, hoverExpand: sidebarHoverExpand, toggleCollapsed: toggleSidebar, toggleHoverExpand: toggleSidebarHoverExpand } = useSidebarPreferences();
  const { enabled: topbarCollapse, toggleEnabled: toggleTopbarCollapse } = useTopbarCollapse();
  const { size: ballSize, setSize: setBallSize } = useBallSize();

  return (
    <article className="personal-card preferences-card">
      <div className="personal-card-title">
        <span className="iconfont icon-skin" aria-hidden="true" />
        <div>
          <h2>偏好设置</h2>
          <small>设置保存在本机，随时可以改回来。</small>
        </div>
      </div>

      <div className="preferences-list">
        <div className="preferences-row">
          <span className="preferences-row-label">悬浮球大小</span>
          <div className="preferences-segment" role="radiogroup" aria-label="悬浮球大小">
            {BALL_SIZE_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={ballSize === option.id}
                className={ballSize === option.id ? "is-active" : ""}
                onClick={() => setBallSize(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="preferences-row">
          <span className="preferences-row-label">点击特效</span>
          <button
            type="button"
            role="switch"
            aria-checked={tap}
            aria-label="点击特效"
            className={tap ? "preferences-switch is-on" : "preferences-switch"}
            onClick={() => setEffects({ tap: !tap })}
          >
            <span aria-hidden="true" />
          </button>
        </div>

        <div className="preferences-row">
          <span className="preferences-row-label">全屏特效</span>
          <button
            type="button"
            role="switch"
            aria-checked={ambient}
            aria-label="全屏特效"
            className={ambient ? "preferences-switch is-on" : "preferences-switch"}
            onClick={() => setEffects({ ambient: !ambient })}
          >
            <span aria-hidden="true" />
          </button>
        </div>

        <div className="preferences-row">
          <span className="preferences-row-label">顶栏滚动收起</span>
          <button
            type="button"
            role="switch"
            aria-checked={topbarCollapse}
            aria-label="顶栏滚动收起"
            className={topbarCollapse ? "preferences-switch is-on" : "preferences-switch"}
            onClick={toggleTopbarCollapse}
          >
            <span aria-hidden="true" />
          </button>
        </div>

        <div className="preferences-row">
          <span className="preferences-row-label">侧边栏收起</span>
          <button
            type="button"
            role="switch"
            aria-checked={sidebarCollapsed}
            aria-label="侧边栏收起"
            className={sidebarCollapsed ? "preferences-switch is-on" : "preferences-switch"}
            onClick={toggleSidebar}
          >
            <span aria-hidden="true" />
          </button>
        </div>

        {/* 子项：仅「侧边栏收起」开启后可用，否则置灰不可点 */}
        <div className={sidebarCollapsed ? "preferences-row is-sub" : "preferences-row is-sub is-locked"}>
          <span className="preferences-row-label">触碰展开</span>
          <button
            type="button"
            role="switch"
            aria-checked={sidebarHoverExpand}
            aria-label="触碰展开"
            disabled={!sidebarCollapsed}
            className={sidebarHoverExpand ? "preferences-switch is-on" : "preferences-switch"}
            onClick={() => {
              if (!sidebarCollapsed) return;
              toggleSidebarHoverExpand();
            }}
          >
            <span aria-hidden="true" />
          </button>
        </div>
      </div>

      <p className="preferences-note">特效开关只停止动画，配色与背景不受影响。</p>
    </article>
  );
}
