import { SPEC_CARD_CORNER_LABELS } from "../data/podCustomizationModel";
import type { SpecCardCorner, SpecCardDisplayUnit, SpecCardStyle } from "../types";

type Props = {
  style: SpecCardStyle;
  corner: SpecCardCorner;
  enabled: boolean;
  displayUnit: SpecCardDisplayUnit;
  onStyleChange: (style: SpecCardStyle) => void;
  onCornerChange: (corner: SpecCardCorner) => void;
  onEnabledChange: (enabled: boolean) => void;
  onDisplayUnitChange: (unit: SpecCardDisplayUnit) => void;
  disabled?: boolean;
};

const STYLE_OPTIONS: Array<{ value: SpecCardStyle; label: string }> = [
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

// 有些品不需要把尺寸印到素材图上：关掉后素材图保持干净母版，长/宽/高数据仍照常导出。
const PRINT_OPTIONS: Array<{ value: boolean; label: string; hint: string }> = [
  { value: true, label: "印到图上", hint: "素材图带尺寸卡片" },
  { value: false, label: "不印", hint: "素材图保持干净" },
];

const DISPLAY_UNIT_OPTIONS: Array<{ value: SpecCardDisplayUnit; label: string; hint: string }> = [
  { value: "cm", label: "厘米", hint: "cm" },
  { value: "in", label: "英寸", hint: "in" },
];

// 2×2 排布与图上实际象限一致：左上 / 右上 / 左下 / 右下。
const CORNER_OPTIONS: SpecCardCorner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];

export function SpecCardAppearanceControls({ style, corner, enabled, displayUnit, onStyleChange, onCornerChange, onEnabledChange, onDisplayUnitChange, disabled = false }: Props) {
  return (
    <section className="pod-spec-card-appearance" aria-label="卡片外观">
      <div className="pod-spec-card-section-title"><span>APPEARANCE</span><h3>卡片外观</h3></div>

      <div className="pod-spec-card-field">
        <span className="pod-spec-card-field-label">是否印到图上<em>*</em></span>
        <div className="pod-spec-card-style-options" role="radiogroup" aria-label="是否印到图上">
          {PRINT_OPTIONS.map((option) => (
            <button
              key={String(option.value)}
              type="button"
              role="radio"
              aria-checked={enabled === option.value}
              className={enabled === option.value ? "is-active" : ""}
              data-print={option.value ? "on" : "off"}
              disabled={disabled}
              onClick={() => onEnabledChange(option.value)}
            >
              <b>{option.label}</b>
              <i aria-hidden="true">{option.hint}</i>
            </button>
          ))}
        </div>
      </div>

      {enabled && (
        <div className="pod-spec-card-field">
          <span className="pod-spec-card-field-label">印图单位<em>*</em></span>
          <div className="pod-spec-card-style-options" role="radiogroup" aria-label="印图单位">
            {DISPLAY_UNIT_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={displayUnit === option.value}
                className={displayUnit === option.value ? "is-active" : ""}
                data-unit={option.value}
                disabled={disabled}
                onClick={() => onDisplayUnitChange(option.value)}
              >
                <b>{option.label}</b>
                <i aria-hidden="true">{option.hint}</i>
              </button>
            ))}
          </div>
        </div>
      )}

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
