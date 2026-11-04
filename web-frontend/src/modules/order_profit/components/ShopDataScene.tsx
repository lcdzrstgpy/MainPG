/**
 * 店铺数据的小场景插画：用于分区标题、采集步骤和账号入口。
 * 与页头共用同一套墨线/纸面变量，但尺寸更小、不盖章，避免抢主视觉。
 */
export type ShopDataSceneVariant =
  | "todo"
  | "sources"
  | "explore"
  | "shop"
  | "link"
  | "prepare"
  | "capture"
  | "result"
  | "team"
  | "finance"
  | "settle"
  | "records"
  | "orders";

export function ShopDataScene({
  variant,
  className = "",
}: {
  variant: ShopDataSceneVariant;
  className?: string;
}) {
  return (
    <svg
      className={`op-art op-scene ${className}`.trim()}
      viewBox="0 0 160 110"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      {SCENES[variant]}
    </svg>
  );
}

const SCENES: Record<ShopDataSceneVariant, JSX.Element> = {
  todo: (
    <>
      <path
        d="M48 16h56a6 6 0 016 6v66a6 6 0 01-6 6H48a6 6 0 01-6-6V22a6 6 0 016-6Z"
        fill="var(--art-paper)"
        stroke="var(--art-ink)"
        strokeWidth="1.4"
      />
      <rect x="64" y="10" width="24" height="12" rx="4" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.3" />
      <g stroke="var(--art-line)" strokeWidth="1.3" fill="none">
        <rect x="54" y="34" width="11" height="11" rx="2" />
        <rect x="54" y="54" width="11" height="11" rx="2" />
        <rect x="54" y="74" width="11" height="11" rx="2" />
        <path d="M56.5 39.5l2 2 3.5-4M56.5 59.5l2 2 3.5-4" />
      </g>
      <path d="M72 39h24M72 59h18M72 79h22" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinecap="round" opacity=".7" />
      <path d="M118 88l17-27" stroke="var(--art-ink)" strokeWidth="2" strokeLinecap="round" />
      <path d="M135 61l4-1-1 4Z" fill="var(--art-seal)" />
      <path d="M116 90l3-5 3 3Z" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.2" />
    </>
  ),
  sources: (
    <>
      <rect x="20" y="32" width="52" height="54" rx="5" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M29 43h34M29 51h26M29 59h32M29 67h20M29 75h28" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="2 4" />
      <g fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4">
        <circle cx="108" cy="24" r="9" />
        <circle cx="134" cy="52" r="9" />
        <circle cx="108" cy="82" r="9" />
      </g>
      <path d="M72 44c12-10 20-16 27-18M72 60h53M72 76c12 4 20 6 27 6" fill="none" stroke="var(--art-line)" strokeWidth="1.2" strokeDasharray="3 4" />
      <path d="M108 33v-4M104 33v-4M134 61v-4M130 61v-4" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="108" cy="24" r="2.4" fill="var(--art-seal)" />
      <circle cx="134" cy="52" r="2.4" fill="var(--art-brand)" />
    </>
  ),
  explore: (
    <>
      <path d="M20 58l20-10 20 10 20-10v34L60 92 40 82 20 92Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M40 48v34M60 58v34" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="3 4" />
      <path d="M28 80c10-14 26-18 40-26" fill="none" stroke="var(--art-seal)" strokeWidth="1.4" strokeDasharray="3 4" />
      <circle cx="106" cy="50" r="26" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" />
      <circle cx="106" cy="50" r="19" fill="none" stroke="var(--art-rule)" strokeWidth="1" />
      <path d="M112 39l-4 12-8 6 4-13 8-5Z" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx="106" cy="50" r="2" fill="var(--art-seal)" />
      <path d="M106 21v4M106 75v4M77 50h4M131 50h4" stroke="var(--art-line)" strokeWidth="1.2" strokeLinecap="round" />
    </>
  ),
  shop: (
    <>
      <path d="M40 86V48h66v38" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M35 48l7-14h62l7 14Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M46 48v-8M57 48v-8M68 48v-8M79 48v-8M90 48v-8" stroke="var(--art-line)" strokeWidth="1" />
      <rect x="64" y="62" width="18" height="24" fill="none" stroke="var(--art-ink)" strokeWidth="1.4" />
      <rect x="45" y="60" width="12" height="9" fill="none" stroke="var(--art-line)" strokeWidth="1.2" />
      <path d="M96 78l20-13 8 4 16-22" fill="none" stroke="var(--art-brand)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M134 43l7-1-2 7" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  link: (
    <>
      <rect x="16" y="26" width="48" height="60" rx="5" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <circle cx="40" cy="44" r="9" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.3" />
      <path d="M30 66c2-6 5-9 10-9s8 3 10 9M26 76h28" stroke="var(--art-line)" strokeWidth="1.3" strokeLinecap="round" fill="none" />
      <rect x="96" y="26" width="48" height="60" rx="5" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <circle cx="120" cy="44" r="9" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.3" />
      <path d="M110 66c2-6 5-9 10-9s8 3 10 9M106 76h28" stroke="var(--art-line)" strokeWidth="1.3" strokeLinecap="round" fill="none" />
      <path d="M68 52h24" stroke="var(--art-seal)" strokeWidth="1.6" strokeDasharray="3 4" />
      <circle cx="68" cy="52" r="5" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.4" />
      <circle cx="92" cy="52" r="5" fill="var(--art-paper)" stroke="var(--art-seal)" strokeWidth="1.4" />
    </>
  ),
  prepare: (
    <>
      <rect x="22" y="20" width="68" height="48" rx="5" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M22 31h68" stroke="var(--art-ink)" strokeWidth="1.2" />
      <circle cx="29" cy="26" r="1.8" fill="var(--art-line)" />
      <circle cx="35" cy="26" r="1.8" fill="var(--art-line)" />
      <rect x="30" y="39" width="26" height="6" rx="3" fill="var(--art-wash)" />
      <path d="M30 52h44M30 59h32" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <path
        d="M112 36a6 6 0 016-6h9v7a4 4 0 010 8v7h-9a6 6 0 01-6-6v-4h-4a4 4 0 010-8h4Z"
        fill="var(--art-wash)"
        stroke="var(--art-ink)"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M100 60l-10 8" stroke="var(--art-line)" strokeWidth="1.2" strokeDasharray="3 3" />
      <g stroke="var(--art-line)" strokeWidth="1.3" fill="none">
        <rect x="96" y="72" width="10" height="10" rx="2" />
        <rect x="96" y="88" width="10" height="10" rx="2" />
        <path d="M98 77l2 2 3-4" />
      </g>
      <path d="M112 77h28M112 93h20" stroke="var(--art-rule)" strokeWidth="1.6" strokeLinecap="round" />
    </>
  ),
  capture: (
    <>
      <path
        d="M48 40c7-10 21-11 28-2 11 1 17 8 16 16-1 8-8 13-17 13H45c-10 0-18-6-18-14 0-9 8-14 21-13Z"
        fill="var(--art-paper)"
        stroke="var(--art-ink)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M68 60v20m0 0-6-6m6 6 6-6" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M28 86h80v8H28Z" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M68 86v8" stroke="var(--art-line)" strokeWidth="1.2" />
      <circle cx="120" cy="84" r="3" fill="var(--art-seal)" />
      <circle cx="132" cy="84" r="3" fill="none" stroke="var(--art-line)" strokeWidth="1.3" />
      <circle cx="144" cy="84" r="3" fill="none" stroke="var(--art-line)" strokeWidth="1.3" />
    </>
  ),
  result: (
    <>
      <circle cx="54" cy="52" r="25" fill="none" stroke="var(--art-rule)" strokeWidth="4" />
      <path d="M54 27a25 25 0 0118 42" fill="none" stroke="var(--art-brand)" strokeWidth="4" strokeLinecap="round" />
      <path d="M42 53l7 7 13-15" fill="none" stroke="var(--art-seal)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <g stroke="var(--art-line)" strokeWidth="1.4" fill="none">
        <rect x="94" y="30" width="10" height="10" rx="2" />
        <rect x="94" y="50" width="10" height="10" rx="2" />
        <rect x="94" y="70" width="10" height="10" rx="2" />
      </g>
      <path d="M110 35h30M110 55h22M110 75h28" stroke="var(--art-ink)" strokeWidth="1.4" strokeLinecap="round" opacity=".65" />
      <path d="M96 35h4M115 55h4M96 75h4" stroke="var(--art-rule)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="134" cy="36" r="3" fill="var(--art-seal)" />
    </>
  ),
  team: (
    <>
      <circle cx="44" cy="40" r="11" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.4" />
      <circle cx="74" cy="30" r="9" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <circle cx="102" cy="40" r="11" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.4" />
      <path d="M33 66c3-8 8-12 11-12s8 4 11 12M65 54c2-6 5-9 9-9s7 3 9 9M91 66c3-8 8-12 11-12s8 4 11 12" fill="none" stroke="var(--art-line)" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M52 86l26-16 8 6 14-20" fill="none" stroke="var(--art-brand)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M93 54l7-1-2 7" fill="none" stroke="var(--art-seal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M30 90h16M118 90h18" stroke="var(--art-rule)" strokeWidth="2" strokeLinecap="round" />
    </>
  ),
  finance: (
    <>
      <rect x="16" y="20" width="54" height="70" rx="4" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M26 34h34" stroke="var(--art-ink)" strokeWidth="1.3" strokeLinecap="round" opacity=".7" />
      <path d="M26 44h34M26 53h34M26 62h34M26 71h34M26 80h34" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="2 4" />
      <path d="M26 62h20" stroke="var(--art-seal)" strokeWidth="1.6" strokeLinecap="round" />
      <rect x="88" y="26" width="58" height="56" rx="4" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M88 48h58" stroke="var(--art-ink)" strokeWidth="1.2" />
      <g stroke="var(--art-line)" strokeWidth="1.1">
        <path d="M98 26v56M110 26v56M122 26v56M134 26v56" />
      </g>
      <g fill="var(--art-seal)">
        <circle cx="98" cy="41" r="3.2" />
        <circle cx="122" cy="41" r="3.2" />
        <circle cx="134" cy="41" r="3.2" />
      </g>
      <g fill="var(--art-brand)">
        <circle cx="110" cy="34" r="3.2" />
        <circle cx="98" cy="60" r="3.2" />
        <circle cx="134" cy="60" r="3.2" />
        <circle cx="122" cy="68" r="3.2" />
      </g>
      <path d="M96 88h50" stroke="var(--art-rule)" strokeWidth="2" strokeLinecap="round" />
    </>
  ),
  settle: (
    <>
      <rect x="18" y="18" width="58" height="72" rx="4" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M28 32h38M28 42h26" stroke="var(--art-line)" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M28 54h38M28 64h38M28 74h24" stroke="var(--art-rule)" strokeWidth="1" strokeDasharray="2 4" />
      <rect x="28" y="80" width="28" height="7" rx="2" fill="var(--art-wash)" />
      <path d="M78 52h18" stroke="var(--art-seal)" strokeWidth="1.6" strokeDasharray="3 3" />
      <ellipse cx="112" cy="84" rx="24" ry="8" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <ellipse cx="112" cy="76" rx="24" ry="8" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <ellipse cx="112" cy="68" rx="24" ry="8" fill="var(--art-wash)" stroke="var(--art-brand)" strokeWidth="1.4" />
      <path d="M100 86v6M124 86v6" stroke="var(--art-line)" strokeWidth="1.2" />
      <path d="M96 40l8 8 14-16" fill="none" stroke="var(--art-seal)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="136" cy="34" r="3" fill="var(--art-seal)" />
    </>
  ),
  records: (
    <>
      <rect x="20" y="28" width="62" height="62" rx="4" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M20 46h62M20 72h62" stroke="var(--art-ink)" strokeWidth="1.2" />
      <rect x="38" y="38" width="20" height="6" rx="2" fill="var(--art-wash)" />
      <rect x="38" y="62" width="26" height="6" rx="2" fill="var(--art-wash)" />
      <path d="M30 82h44" stroke="var(--art-rule)" strokeWidth="2" strokeLinecap="round" />
      <path d="M76 34h12M76 50h8" stroke="var(--art-line)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="118" cy="50" r="24" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.5" />
      <path d="M118 32v18l11 7" fill="none" stroke="var(--art-brand)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="118" cy="50" r="2.4" fill="var(--art-seal)" />
      <path d="M118 18v4M118 78v4M86 50h4M146 50h4" stroke="var(--art-line)" strokeWidth="1.2" strokeLinecap="round" />
    </>
  ),
  orders: (
    <>
      <path d="M24 50l26-13 26 13v32L50 96 24 82Z" fill="var(--art-wash)" stroke="var(--art-ink)" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M24 50l26 13 26-13M50 63v33" fill="none" stroke="var(--art-ink)" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M37 43l26 13" stroke="var(--art-line)" strokeWidth="1.2" />
      <rect x="92" y="34" width="46" height="54" rx="3" fill="var(--art-paper)" stroke="var(--art-ink)" strokeWidth="1.4" />
      <path d="M100 44h30" stroke="var(--art-line)" strokeWidth="1.3" strokeLinecap="round" />
      <g stroke="var(--art-ink)" strokeWidth="1.4">
        <path d="M100 56v18M104 56v18M109 56v18M114 56v18M120 56v18M124 56v18M129 56v18" />
      </g>
      <path d="M100 78h30" stroke="var(--art-line)" strokeWidth="1.1" />
      <path d="M96 24l16 6" stroke="var(--art-seal)" strokeWidth="1.6" strokeDasharray="3 3" />
      <circle cx="138" cy="26" r="4" fill="none" stroke="var(--art-seal)" strokeWidth="1.4" />
    </>
  ),
};
