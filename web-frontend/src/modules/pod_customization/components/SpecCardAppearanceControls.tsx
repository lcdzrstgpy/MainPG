import { SPEC_CARD_CORNER_LABELS } from "../data/podCustomizationModel";
import type { SpecCardCorner, SpecCardStyle } from "../types";

type Props = {
  style: SpecCardStyle;
  corner: SpecCardCorner;
  onStyleChange: (style: SpecCardStyle) => void;
  onCornerChange: (corner: SpecCardCorner) => void;
  disabled?: boolean;
};

const STYLE_OPTIONS: Array<{ value: SpecCardStyle; label: string }> = [
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

// 2×2 排布与图上实际象限一致：左上 / 右上 / 左下 / 右下。
const CORNER_OPTIONS: SpecCardCorner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];

export function SpecCardAppearanceControls({ style, corner, onStyleChange, onCornerChange, disabled = false }: Props) {
  return (
    <section className="pod-spec-card-appearance" aria-label="卡片外观">
      <div className="pod-spec-card-section-title"><span>APPEARANCE</span><h3>卡片外观</h3></div>

      <div className="pod-spec-card-field">
        <span className="pod-spec-card-field-label">卡片风格<em>*</em></span>
        <div className="pod-spec-card-style-options" role="radiogroup" aria-label="卡片风格">
          {STYLE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={style === option.value}
              className={style === option.value ? "is-active" : ""}
              data-style={option.value}
              disabled={disabled}
              onClick={() => onStyleChange(option.value)}
            >
              <b>{option.label}</b>
              <i aria-hidden="true">{option.value === "light" ? "白底深字" : "深底白字"}</i>
            </button>
          ))}
        </div>
      </div>

      <div className="pod-spec-card-field">
        <span className="pod-spec-card-field-label">卡片位置<em>*</em></span>
        <div className="pod-spec-card-corner-grid" role="radiogroup" aria-label="卡片位置">
          {CORNER_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={corner === option}
              className={corner === option ? "is-active" : ""}
              data-corner={option}
              disabled={disabled}
              onClick={() => onCornerChange(option)}
            >{SPEC_CARD_CORNER_LABELS[option]}</button>
          ))}
        </div>
      </div>
    </section>
  );
}
