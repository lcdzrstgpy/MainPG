import { httpJson } from "../../../transport/http/client";

export type PricingSubItem = {
  charge_points: number;
  charge_units: number;
  intercept_refund_ratio: number;
  no_return_refund_ratio: number;
};

export type PricingItemsPayload = {
  ok: boolean;
  pricing: {
    rule_version: number;
    point_unit_scale: number;
    max_charge_per_link: number;
    max_charge_units_per_link: number;
    freeze_per_link: number;
    freeze_units_per_link: number;
    ttl_days: number;
    items: Record<string, PricingSubItem>;
    effective_at: string;
  };
};

export type PodPricingItemsPayload = {
  ok: boolean;
  pricing: {
    rule_version: number;
    point_unit_scale: number;
    items: Record<string, Pick<PricingSubItem, "charge_points" | "charge_units">>;
    effective_at: string;
  };
};

export type PricingChangelogEntry = {
  id: number;
  rule_version: number;
  changed_by: string;
  change_reason: string;
  before: unknown;
  after: unknown;
  created_at: string;
};

export type KeyGrant = {
  grant_id: string;
  account_id: string;
  workspace_id: string;
  freeze_id: string;
  provider: string;
  key_label: string;
  granted_at: string;
  expires_at: string;
  revoked_at: string;
};

export function loadPricingItems() {
  return httpJson<PricingItemsPayload>("/api/admin/billing/pricing/items");
}

export function updatePricingItems(payload: {
  items: Record<string, { charge_points: number }>;
  change_reason: string;
}) {
  return httpJson<PricingItemsPayload>("/api/admin/billing/pricing/items", {
    method: "PUT",
    body: payload,
  });
}

export function loadPodPricingItems() {
  return httpJson<PodPricingItemsPayload>("/api/admin/billing/pricing/pod");
}

export function updatePodPricingItems(payload: {
  items: Record<string, { charge_points: number }>;
  change_reason: string;
}) {
  return httpJson<PodPricingItemsPayload>("/api/admin/billing/pricing/pod", {
    method: "PUT",
    body: payload,
  });
}

export function loadPricingChangelog(limit = 50) {
  return httpJson<{ ok: boolean; items: PricingChangelogEntry[] }>(
    `/api/admin/billing/pricing/changelog?limit=${limit}`,
  );
}

export function loadKeyGrants(limit = 100) {
  return httpJson<{ ok: boolean; items: KeyGrant[] }>(
    `/api/admin/billing/keys/grants?limit=${limit}`,
  );
}
