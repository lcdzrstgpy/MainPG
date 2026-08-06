export type ProfitActivitySite = "US" | "CO" | "EC";

export type ProfitActivityScope = "default" | "company";

export type ProfitActivityProduct = {
  id?: number;
  skc: string;
  site?: ProfitActivitySite;
  site_code?: ProfitActivitySite;
  workspace_name?: string;
  created_by?: string;
  created_by_username?: string;
  selling_price?: number;
  cost_price?: number;
  weight_kg?: number;
  net_profit?: number;
  profit_rate?: number;
  source_url?: string;
  note?: string;
  image_path?: string;
  source_image_path?: string;
  is_owner?: boolean;
  can_edit?: boolean;
};

export type ProductQueryParams = {
  site: ProfitActivitySite;
  scope: ProfitActivityScope;
  skcs: string;
};
