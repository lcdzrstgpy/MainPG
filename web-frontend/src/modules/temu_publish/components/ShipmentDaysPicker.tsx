/**
 * 「承诺发货时效」选择器（发布页 · 包装信息 / 批量发布弹窗共用）。
 *
 * 原来是一个裸的数字输入框，只有一个 placeholder=9 在暗示默认值，
 * 用户看不出 Temu 侧「1/2/7/9 个工作日」是个固定档位（后台建品页就是这四个 radio），
 * 也不知道 9 天是预设。改成和 Temu 后台一致的四个档位 + 自定义兜底。
 *
 * 默认 9 天仍是最重要的约束：`shipmentLimitSecond = 777600`，店里 230 件商品里
 * 213 件都是它。1/2/7 天是 Temu 允许的选项，但 POD/定制实际做不到，界面上不拦、只靠
 * 字段说明提醒。
 *
 * 值统一用「天」的字符串：上层直接 `Number(value) * 86400` 折算秒数，
 * 自定义档也走同一条路径，不需要额外分支。
 */

import { useId } from "react";

/** Temu 后台提供的四个固定档位（个工作日）。 */
export const SHIPMENT_DAY_OPTIONS = ["1", "2", "7", "9"] as const;

/**
 * 默认发货时效（天）。
 *
 * 用 **9 天**，就是 Temu 半托管的预设值（`shipmentLimitSecond = 777600`）。
 * 依据（2026-11-03 扫店铺 230 件商品）：213 件历史商品全是 777600，服务商文档里的
 * 发布成功案例也是 777600。之前我们默认 7 天（604800）是自己拍的，跟店里其他商品不一致。
 *
 * 别再往下调成默认：1 天等于向 Temu 承诺 24 小时发货，POD/定制做不到，会被判延迟发货。
 */
export const DEFAULT_SHIPMENT_DAYS = "9";

type Props = {
  /** 当前值，单位「天」的字符串（"9" / "12"）；空串表示自定义档还没填 */
  value: string;
  onChange: (value: string) => void;
};

export function ShipmentDaysPicker({ value, onChange }: Props) {
  // 同一页可能渲染多个（发布页 + 批量弹窗），radio 的 name 必须各自独立，
  // 否则两边会互相取消选中。
  const group = useId();
  const isPreset = (SHIPMENT_DAY_OPTIONS as readonly string[]).includes(value);
  const custom = !isPreset;

  return (
    <div className="temu-ship-pick">
      {SHIPMENT_DAY_OPTIONS.map((day) => (
        <label key={day} className="temu-ship-pick-opt">
          <input
            type="radio"
            name={group}
            checked={value === day}
            onChange={() => onChange(day)}
          />
          <b>{day}</b>
          <span>个工作日内发货</span>
        </label>
      ))}
      <label className="temu-ship-pick-opt">
        <input
          type="radio"
          name={group}
          checked={custom}
          // 已经是自定义档时再点一次不重置，免得把刚填的数字清掉
          onChange={() => {
            if (!custom) onChange("");
          }}
        />
        <span>自定义</span>
      </label>
      {custom && (
        <span className="temu-ship-pick-input">
          <input
            className="temu-input"
            inputMode="numeric"
            value={value}
            // 只留数字：这个值最后要换算成秒，混进「9天」会被 parsePositive 拦下
            onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, ""))}
            placeholder="如 12"
            aria-label="自定义发货时效（天）"
          />
          <em>天</em>
        </span>
      )}
    </div>
  );
}
