/**
 * 店铺数据的细线图标：用于指标、分区标题和折叠区。
 * 与页头插画同源——1.5px 墨线、圆角端点、无填充，跟随文字颜色。
 */
export type ShopDataGlyphName =
  | "orders"
  | "box"
  | "truck"
  | "chart"
  | "eye"
  | "cursor"
  | "visitor"
  | "money"
  | "profit"
  | "wallet"
  | "ad"
  | "penalty"
  | "clock"
  | "archive"
  | "key"
  | "cloud"
  | "refresh"
  | "warning"
  | "check"
  | "source"
  | "compass"
  | "calendar"
  | "search"
  | "shield"
  | "link"
  | "team"
  | "trend"
  | "channel"
  | "ledger"
  | "cost"
  | "receipt";

export function ShopDataGlyph({
  name,
  className = "",
}: {
  name: ShopDataGlyphName;
  className?: string;
}) {
  return (
    <svg
      className={`op-glyph ${className}`.trim()}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {GLYPHS[name]}
    </svg>
  );
}

const GLYPHS: Record<ShopDataGlyphName, JSX.Element> = {
  orders: (
    <>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M8.5 8h7M8.5 11.5h7M8.5 15h4" />
    </>
  ),
  box: (
    <>
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3Z" />
      <path d="M4 7.5l8 4.5 8-4.5M12 12v9" />
    </>
  ),
  truck: (
    <>
      <path d="M3 6h11v10H3zM14 9h4l3 3v4h-7" />
      <circle cx="7" cy="18" r="2" />
      <circle cx="17.5" cy="18" r="2" />
    </>
  ),
  chart: (
    <>
      <path d="M4 20V4M4 20h16" />
      <path d="M7 15l4-5 3 3 5-7" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
  cursor: <path d="M6 3l12 8-5 1.5L10 18 6 3Z" />,
  visitor: (
    <>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5 20c1-4 3.5-6 7-6s6 2 7 6" />
    </>
  ),
  money: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M9 8l3 4 3-4M12 12v5M9.5 13.5h5M9.5 16h5" />
    </>
  ),
  profit: (
    <>
      <path d="M4 18l5-6 4 3 6-8" />
      <path d="M15 7h4v4" />
    </>
  ),
  wallet: (
    <>
      <rect x="3" y="6" width="18" height="13" rx="2" />
      <path d="M3 9h18M16 13h2" />
    </>
  ),
  ad: (
    <>
      <path d="M4 10v4l10 4V6L4 10Z" />
      <path d="M14 8l4-3v14l-4-3M7 15v3" />
    </>
  ),
  penalty: (
    <>
      <path d="M12 4l9 15H3L12 4Z" />
      <path d="M12 9v4M12 16h.01" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="5" width="18" height="4" rx="1" />
      <rect x="5" y="9" width="14" height="10" rx="1" />
      <path d="M9 13h6" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="12" r="3" />
      <path d="M11 12h9M17 12v3M14 12v2" />
    </>
  ),
  cloud: (
    <>
      <path d="M7 16a4 4 0 010-8 5 5 0 019.6 1.4A3.5 3.5 0 0117 16H7Z" />
      <path d="M12 11v6m0 0-2.5-2.5M12 17l2.5-2.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M4 10a8 8 0 0114-3l2 2" />
      <path d="M20 5v4h-4" />
      <path d="M20 14a8 8 0 01-14 3l-2-2" />
      <path d="M4 19v-4h4" />
    </>
  ),
  warning: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5h.01" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12l2.5 3L16 9" />
    </>
  ),
  source: (
    <>
      <circle cx="6" cy="6" r="2" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="12" cy="18" r="2" />
      <path d="M8 6h8M7 8l4 8M17 8l-4 8" />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M15.5 8.5l-2 5-5 2 2-5 5-2Z" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4 4" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3Z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 010-5.7l2-2a4 4 0 015.7 5.7l-1.5 1.5" />
      <path d="M14 10a4 4 0 010 5.7l-2 2A4 4 0 016.3 12l1.5-1.5" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="9" r="3" />
      <circle cx="17" cy="10" r="2.3" />
      <path d="M3 20c.8-4 3-6 6-6s5.2 2 6 6M15 14c2 0 3.5 1.5 4.5 4" />
    </>
  ),
  trend: (
    <>
      <path d="M4 19l5-6 3.5 2.5L19 6" />
      <path d="M15 6h4v4" />
    </>
  ),
  channel: (
    <>
      <path d="M4 12h5l4-6h7M9 12l4 6h7" />
      <circle cx="4" cy="12" r="1.5" />
    </>
  ),
  ledger: (
    <>
      <path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3V4Z" />
      <path d="M8 4v16M11 9h5M11 13h5" />
    </>
  ),
  cost: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8 7h8v4H8zM8 14h2M8 17h2M12 14h2M12 17h2M16 14h.01M16 17h.01" />
    </>
  ),
  receipt: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 7h6M9 11h6M9 15h4" />
    </>
  ),
};
