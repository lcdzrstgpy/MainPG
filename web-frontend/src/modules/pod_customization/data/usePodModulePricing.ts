import { useEffect, useState } from "react";
import { httpJson } from "../../../transport/http/client";

/**
 * POD 四模块的实时单价：服务端在 `/api/customer/billing/summary` 的
 * `pricing.pod_modules` 里按计费画像下发（积分口径）。
 *
 * 四个模块页面共用本模块级缓存：同一运行时内只拉一次，5 分钟后过期；
 * 拉取失败静默返回空，由调用方隐藏价格，不阻塞页面。
 */

export type PodModuleKey = "pod_random_v1" | "pod_semi_v1" | "pod_replica_v1" | "pod_fission_v1";

export type PodModulePricing = {
  min: number;
  max: number;
  /** 计价单位：半定制按「组」（4 款 = 1 组），其余按「款」。 */
  unit: string;
};

type RawPodModule = {
  key?: string;
  charge_min_points?: number;
  charge_max_points?: number;
};

const CACHE_TTL_MS = 5 * 60 * 1000;
const DEFAULT_UNIT = "款";
const UNIT_BY_KEY: Record<string, string> = { pod_semi_v1: "组" };

let cache: { at: number; modules: RawPodModule[] } | null = null;
let inflight: Promise<RawPodModule[]> | null = null;

async function loadPodModules(): Promise<RawPodModule[]> {
  if (cache && Date.now() - cache.at < CACHE_TTL_MS) return cache.modules;
  if (inflight) return inflight;
  inflight = httpJson<{ pricing?: { pod_modules?: RawPodModule[] } }>("/api/customer/billing/summary")
    .then((summary) => {
      const modules = Array.isArray(summary?.pricing?.pod_modules)
        ? summary.pricing.pod_modules
        : [];
      cache = { at: Date.now(), modules };
      return modules;
    })
    .catch(() => [])
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function usePodModulePricing(key: PodModuleKey): PodModulePricing | null {
  const [pricing, setPricing] = useState<PodModulePricing | null>(null);

  useEffect(() => {
    let alive = true;
    void loadPodModules().then((modules) => {
      if (!alive) return;
      const match = modules.find((item) => String(item?.key || "") === key);
      const min = Number(match?.charge_min_points);
      const max = Number(match?.charge_max_points);
      if (!Number.isFinite(min) || !Number.isFinite(max)) {
        setPricing(null);
        return;
      }
      setPricing({ min, max, unit: UNIT_BY_KEY[key] ?? DEFAULT_UNIT });
    });
    return () => {
      alive = false;
    };
  }, [key]);

  return pricing;
}

/** 固定价显示一个数，区间价显示 `min~max`；服务端没下发时返回空串。 */
export function formatPodModulePrice(pricing: PodModulePricing | null): string {
  if (!pricing) return "";
  const format = (value: number) => (Number.isInteger(value) ? value.toLocaleString() : String(value));
  const amount =
    pricing.min === pricing.max ? format(pricing.min) : `${format(pricing.min)}~${format(pricing.max)}`;
  return `当前价格：${amount} 积分/${pricing.unit}`;
}
