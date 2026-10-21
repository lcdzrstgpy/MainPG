import { scriptStyleLabel } from "../lib/creation-brief-defaults";
import type { ScriptStyleRequirement } from "../lib/script-style-requirement";

type StyleChoicePromptProps = {
  requirement: ScriptStyleRequirement;
  busy?: boolean;
  onPick: (styleType: string) => void;
  onClose: () => void;
};

/** 409 `needs_explicit_style` 专用弹层：把候选风格翻成中文让用户点选，再重试同一请求。 */
export function StyleChoicePrompt({ requirement, busy, onPick, onClose }: StyleChoicePromptProps) {
  return (
    <div className="ai-video-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="ai-video-modal"
        role="dialog"
        aria-modal="true"
        aria-label="选择脚本风格"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="ai-video-modal-head">
          <h3>请选择脚本风格</h3>
          <button type="button" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        <p>
          {requirement.reason === "missing"
            ? "还没有选择脚本风格。选一个后继续创建，表单内容不会丢失。"
            : "历史投放数据不足，无法自动推荐风格。请从下面选一个后继续；项目已经创建，不用重新填写。"}
        </p>
        <div className="ai-video-chip-row">
          {requirement.candidates.map((style) => (
            <button
              key={style}
              type="button"
              className="ai-video-chip"
              disabled={busy}
              onClick={() => onPick(style)}
            >
              {scriptStyleLabel(style)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
