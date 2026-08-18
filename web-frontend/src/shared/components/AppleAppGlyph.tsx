type AppleAppGlyphProps = {
  name: string;
};

export function AppleAppGlyph({ name }: AppleAppGlyphProps) {
  const common = {
    className: "apple-app-glyph",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "dashboard":
      return <svg {...common}><path d="M4.5 15.5a8 8 0 1 1 15 0" /><path d="m12 12 4.2-3.2" /><circle cx="12" cy="12" r="1.4" /><path d="M7 17.5h10" /></svg>;
    case "product_workflow":
    case "product_processing":
      return <svg {...common}><rect x="4" y="5" width="7" height="6" rx="1.5" /><rect x="13" y="5" width="7" height="6" rx="1.5" /><rect x="4" y="13" width="7" height="6" rx="1.5" /><path d="M16.5 13v6M13.5 16h6" /></svg>;
    case "sourcing_workflow":
    case "price_verification":
      return <svg {...common}><path d="M6 3.8h8l4 4V13" /><path d="M14 3.8V8h4" /><path d="M6 3.8v16.4h6" /><path d="M8.7 11h4.6M8.7 14h2.8" /><circle cx="16" cy="17" r="3" /><path d="m18.2 19.2 2 2" /></svg>;
    case "daily_selection":
      return <svg {...common}><circle cx="12" cy="12" r="8" /><path d="m15.5 8.5-2.1 4.9-4.9 2.1 2.1-4.9 4.9-2.1Z" /><circle cx="12" cy="12" r=".7" fill="currentColor" stroke="none" /></svg>;
    case "product_processing_history":
      return <svg {...common}><path d="M4.7 8.2V4.6M4.7 4.6h3.6" /><path d="M5.3 5.2A8 8 0 1 1 4 14" /><path d="M12 7.5V12l3 1.8" /></svg>;
    case "dimension_canvas":
      return <svg {...common}><path d="M4 7h16v10H4z" /><path d="M7 7v3M10 7v2M13 7v3M16 7v2" /><path d="M7 14h10" /></svg>;
    case "profit_activity":
      return <svg {...common}><path d="M5 19V9M12 19V5M19 19v-7" /><path d="M3.5 19.5h17" /><path d="m5 7 6-4 7 5" /></svg>;
    case "profit_activity_products":
      return <svg {...common}><path d="M4.5 7.5h15v11h-15z" /><path d="M3.5 4.5h17v3h-17z" /><path d="M9.5 11h5" /></svg>;
    case "ai_service":
      return <svg {...common}><path d="m12 3 1.2 4.1L17 8.5l-3.8 1.4L12 14l-1.2-4.1L7 8.5l3.8-1.4L12 3Z" /><path d="m18.5 13 .7 2.3 2.1.7-2.1.8-.7 2.2-.7-2.2-2.1-.8 2.1-.7.7-2.3Z" /><path d="m5.5 13 .6 1.8 1.7.7-1.7.6-.6 1.9-.6-1.9-1.7-.6 1.7-.7.6-1.8Z" /></svg>;
    case "personal_center":
      return <svg {...common}><circle cx="12" cy="8" r="3.2" /><path d="M5.5 20c.5-4 2.7-6 6.5-6s6 2 6.5 6" /></svg>;
    case "basic_settings":
      return <svg {...common}><circle cx="12" cy="12" r="6.8" /><circle cx="12" cy="12" r="2.8" /><path d="M12 2.5v2.7M12 18.8v2.7M2.5 12h2.7M18.8 12h2.7M5.3 5.3l1.9 1.9M16.8 16.8l1.9 1.9M18.7 5.3l-1.9 1.9M7.2 16.8l-1.9 1.9" /></svg>;
    default:
      return <svg {...common}><rect x="4" y="4" width="6" height="6" rx="1.4" /><rect x="14" y="4" width="6" height="6" rx="1.4" /><rect x="4" y="14" width="6" height="6" rx="1.4" /><rect x="14" y="14" width="6" height="6" rx="1.4" /></svg>;
  }
}
