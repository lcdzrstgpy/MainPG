export type ProfitActivitySite = string;

export type ProfitActivityScope = "default" | "company";

export type ProfitActivityProduct = {
  id?: number;
  skc: string;
  /** Generic product identifier. skc stays for compatibility with existing APIs. */
  product_id?: string;
  product_id_label?: string;
  site?: ProfitActivitySite;
  site_code?: ProfitActivitySite;
  source_type?: "manual" | "price_verification" | string;
  workspace_name?: string;
  created_by?: string;
  created_by_username?: string;
  selling_price?: number;
  cost_price?: number;
  weight_kg?: number;
  net_profit?: number;
  profit_rate?: number;
  source_url?: string;
  source_groups?: Array<{ source_url?: string; image_paths?: string[]; cost?: number | null }>;
  note?: string;
  image_path?: string;
  attachment_image_path?: string;
  source_main_image_url?: string;
  source_image_path?: string;
  library_created_at?: string;
  created_at?: string;
  updated_at?: string;
  is_owner?: boolean;
  can_edit?: boolean;
};

export type ProductQueryParams = {
  site: ProfitActivitySite;
  scope: ProfitActivityScope;
  skcs: string;
  productIds?: string;
};

export type ProductSourceLink = {
  id: number;
  batch_id: string;
  skc_id: string;
  offer_id: string;
  source_url: string;
  source_title: string;
  main_image_url: string;
  image_paths?: string[];
  group?: number;
  price_cny?: string | number | null;
  moq?: string | number | null;
  domestic_freight_cny?: string | number | null;
  source_decision: string;
  note?: string;
  status?: string;
};

export type ProductSources = {
  skc: string;
  site: ProfitActivitySite;
  product_title?: string;
  selling_price?: number | null;
  links: ProductSourceLink[];
};
