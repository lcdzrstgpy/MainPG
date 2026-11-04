import type { ReactNode } from "react";

/**
 * 店铺数据插画系统：账页 + 朱印。
 *
 * 设计取向：不做柔光渐变和大色块，改用「账页纸 + 细墨线 + 朱砂印章」的编辑感表达，
 * 与水墨青黛主题一致；换到其他主题时仍是一套克制的线稿，而不是通用 AI 插画。
 * 只使用主题变量着色，不依赖外部图片。
 */
export type ShopDataArtVariant =
  | "overview"
  | "traffic"
  | "sales"
  | "orders"
  | "funds"
  | "capture"
  | "records"
  | "accounts"
  | "empty";

const SEAL_TEXT: Record<ShopDataArtVariant, string> = {
  overview: "经",
  traffic: "流",
  sales: "销",
  orders: "单",
  funds: "资",
  capture: "采",
  records: "录",
  accounts: "号",
  empty: "待",
};

export function ShopDataArt({
  variant,
  className = "",
  seal = true,
}: {
  variant: ShopDataArtVariant;
  className?: string;
  /** 空状态 / 页内辅助插画不重复盖章，避免同一屏出现两枚朱印。 */
  seal?: boolean;
}) {
  return (
    <svg
      className={`op-art ${className}`.trim()}
      viewBox="24 10 204 140"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      {/* 纸面横线：从账页后面透出来，只露两侧，形成「纸」的暗示 */}
      <g stroke="var(--art-rule)" strokeWidth="1" opacity=".7">
        <path d="M16 38h208M16 82h208M16 126h208" />
      </g>
      <g stroke="var(--art-line)" strokeWidth="1" strokeLinecap="round" opacity=".5">
        <path d="M24 138l7-7M31 142l7-7M38 146l7-7" />
      </g>

      <path
        d="M56 20h116l16 16v82a8 8 0 0 1-8 8H56a8 8 0 0 1-8-8V28a8 8 0 0 1 8-8Z"
        fill="var(--art-paper)"
        stroke="var(--art-line)"
        strokeWidth="1.4"
      />
      <path d="M172 20v18h16" fill="none" stroke="var(--art-line)" strokeWidth="1.2" />
      <path d="M60 44h84" stroke="var(--art-ink)" strokeWidth="2" strokeLinecap="round" opacity=".72" />
      <path d="M60 53h112" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="2 5" />
      <path d="M60 118h112" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="2 5" />

      <g className="op-object">{ART[variant]}</g>

      {/* 朱印：每张插画一枚，字随板块变化；空状态不重复盖章 */}
      {seal ? (
        <g className="op-seal" transform="rotate(-4 194 40)">
          <rect x="175" y="21" width="38" height="38" rx="5" fill="var(--art-seal)" />
          <rect
            x="180"
            y="26"
            width="28"
            height="28"
            rx="2"
            fill="none"
            stroke="var(--art-seal-ink)"
            strokeWidth="1"
            opacity=".72"
          />
          <text className="op-seal-text" x="194" y="45.5">
            {SEAL_TEXT[variant]}
          </text>
        </g>
      ) : null}
    </svg>
  );
}

const ART: Record<ShopDataArtVariant, ReactNode> = {
  overview: (
    <>
      <path
        d="M70 104l17-10 12 6 15-14 12 6 16-15"
        fill="none"
        stroke="var(--art-brand)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M136 76l8-1-2 8" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M88 110V88h54v22" fill="none" stroke="var(--art-ink)" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M84 88l6-11h50l6 11Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M94 88v-7M104 88v-7M114 88v-7M124 88v-7M134 88v-7" stroke="var(--art-line)" strokeWidth="1" />
      <rect x="108" y="97" width="14" height="13" fill="none" stroke="var(--art-ink)" strokeWidth="1.4" />
      <rect x="93" y="95" width="10" height="7" fill="none" stroke="var(--art-line)" strokeWidth="1.2" />
    </>
  ),
  traffic: (
    <>
      <path
        d="M78 66h68l-23 29v23l-14 6V95Z"
        fill="var(--art-paper)"
        stroke="var(--art-ink)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M92 79h40M102 91h22" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="3 4" />
      <path d="M68 54v10m0 0-4-4m4 4 4-4" fill="none" stroke="var(--art-seal)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M118 54v10m0 0-4-4m4 4 4-4" fill="none" stroke="var(--art-brand)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M168 54v10m0 0-4-4m4 4 4-4" fill="none" stroke="var(--art-line)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="68" cy="50" r="2.6" fill="var(--art-seal)" />
      <circle cx="118" cy="50" r="2.6" fill="var(--art-brand)" />
      <circle cx="168" cy="50" r="2.6" fill="var(--art-line)" />
      <path d="M158 104l8-6 8 8" fill="none" stroke="var(--art-seal)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  sales: (
    <>
      <path d="M70 114h100" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M78 90h30v24H78Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M78 90l5-6h20l5 6Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M114 82h28v32h-28Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M114 82l5-6h18l5 6Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M88 101h10M124 94h8M124 101h8" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="3 3" />
      <path d="M64 76l22-8 20 5 24-13" fill="none" stroke="var(--art-brand)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M124 60l8-2-1 8" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M152 100h16l6 6-6 6h-16Z" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx="158" cy="106" r="1.6" fill="var(--art-seal)" />
    </>
  ),
  orders: (
    <>
      <path d="M64 68h78" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="4 4" />
      <circle cx="64" cy="68" r="3" fill="var(--art-seal)" />
      <circle cx="142" cy="68" r="3" fill="none" stroke="var(--art-line)" strokeWidth="1.2" />
      <path d="M78 82h44v30H78Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M76 82l5-7h48l5 7Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M96 75v37" stroke="var(--art-line)" strokeWidth="1.2" strokeDasharray="3 3" />
      <path d="M84 90h8M84 97h8" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M116 94h34v20h-34Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M121 100h22M121 106h15" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M150 90l7 4-7 4" fill="none" stroke="var(--art-seal)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  funds: (
    <>
      <rect x="86" y="62" width="66" height="50" rx="3" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" />
      <path d="M86 86h66" stroke="var(--art-seal)" strokeWidth="1.6" />
      <path d="M96 66v42M110 66v42M124 66v42M138 66v42" stroke="var(--art-line)" strokeWidth="1.2" />
      <g fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.2">
        <circle cx="96" cy="74" r="3.2" />
        <circle cx="110" cy="74" r="3.2" />
        <circle cx="124" cy="74" r="3.2" />
        <circle cx="110" cy="94" r="3.2" />
        <circle cx="124" cy="94" r="3.2" />
        <circle cx="138" cy="94" r="3.2" />
        <circle cx="96" cy="102" r="3.2" />
        <circle cx="124" cy="102" r="3.2" />
      </g>
      <circle cx="160" cy="108" r="9" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.4" />
      <path d="M160 102v6l4 2" fill="none" stroke="var(--art-seal)" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),
  capture: (
    <>
      <path
        d="M104 62c7-9 22-10 29-1 11 1 17 8 16 17-1 8-8 13-17 13h-30c-10 0-18-6-18-15 0-8 8-14 20-14Z"
        fill="var(--art-paper)"
        stroke="var(--art-ink)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M118 82v18m0 0-6-6m6 6 6-6" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M82 104h64v14H82Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M114 104v14" stroke="var(--art-line)" strokeWidth="1.2" />
      <path d="M88 111h20M122 111h18" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="3 3" />
      <path d="M84 74l-7-4M82 84h-9M154 72l7-5M156 84h9" stroke="var(--art-line)" strokeWidth="1.2" strokeLinecap="round" />
    </>
  ),
  records: (
    <>
      <path d="M74 76h72v14H74Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M79 92h72v14H79Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M84 108h68v12H84Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M86 82h10M91 98h10M96 114h10" stroke="var(--art-rule)" strokeWidth="2" strokeLinecap="round" />
      <path d="M118 82h18M123 98h18M128 114h14" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="156" cy="80" r="11" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.4" />
      <path d="M156 73v8l5 3" fill="none" stroke="var(--art-seal)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  accounts: (
    <>
      <rect x="72" y="64" width="84" height="48" rx="4" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" />
      <path d="M72 76h84" stroke="var(--art-ink)" strokeWidth="1.2" />
      <circle cx="79" cy="70" r="1.8" fill="var(--art-line)" />
      <circle cx="85" cy="70" r="1.8" fill="var(--art-line)" />
      <circle cx="96" cy="94" r="9" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.4" />
      <circle cx="96" cy="91" r="3.2" fill="var(--art-paper)" stroke="var(--art-brand)" strokeWidth="1.2" />
      <path d="M90 101c1-4 3-6 6-6s5 2 6 6" fill="none" stroke="var(--art-brand)" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M112 88h28M112 96h20M112 104h24" stroke="var(--art-line)" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="160" cy="106" r="5" fill="none" stroke="var(--art-seal)" strokeWidth="1.8" />
      <path d="M164 106h14m-4 0v4m-4-4v5" fill="none" stroke="var(--art-seal)" strokeWidth="1.6" strokeLinecap="round" />
    </>
  ),
  empty: (
    <>
      <path d="M92 70h44l10 10v30H92Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M136 70v10h10" fill="none" stroke="var(--art-ink)" strokeWidth="1.2" />
      <path d="M100 84h30M100 92h24M100 100h18" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="3 4" />
      <path d="M150 112l16-28" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M166 84l4-2-1 4Z" fill="var(--art-seal)" stroke="var(--art-seal)" strokeWidth="1" strokeLinejoin="round" />
      <path d="M148 114l3-5 3 3Z" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.2" strokeLinejoin="round" />
      <circle cx="86" cy="108" r="3" fill="var(--art-seal)" opacity=".75" />
      <circle cx="78" cy="116" r="1.6" fill="var(--art-line)" />
    </>
  ),
};
