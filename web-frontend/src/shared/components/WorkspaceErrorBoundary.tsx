import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  /** 变化时自动清掉错误（切换模块/页签后把用户放回来，不用整页刷新） */
  resetKey?: string;
  /** 出错时显示的位置说明，例如「发布历史」 */
  scope?: string;
};

type State = { error: Error | null };

/**
 * 页面级错误边界。
 *
 * 为什么要有：React 里任何一个渲染期异常都会把整棵树卸载 —— 用户看到的是
 * 一整片空白（2026-11-04 发布历史漏登记 workspacePageModules，点一下整个工作台
 * 就白了这个样子）。有边界后最差也只是当前这一页显示一句「出错了」，侧栏和
 * 其它模块还能继续用，而且错误信息留在控制台，便于定位。
 */
export class WorkspaceErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error(`[workspace] ${this.props.scope || "页面"}渲染出错`, error, info.componentStack);
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="module-placeholder">
        <section className="module-empty-card">
          <span className="empty-orbit" />
          <div>
            <h1>{this.props.scope ? `${this.props.scope}没能显示出来` : "这个页面没能显示出来"}</h1>
            <p>返回别的页面继续用不受影响；换个页签会自动重试，也可以点下面的按钮。</p>
            <p style={{ opacity: 0.6, fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>
              {error.message || String(error)}
            </p>
            <button
              type="button"
              style={{ marginTop: 12 }}
              onClick={() => this.setState({ error: null })}
            >
              重试
            </button>
          </div>
        </section>
      </div>
    );
  }
}
