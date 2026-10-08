"use client";

import { useEffect } from "react";
import { configuredDefaultsPatch, type ConfiguredDefaults } from "@/lib/configured-defaults";
import { useSettingsStore } from "@/lib/stores/settings-store";

/**
 * 把运维下发的默认设置（平台 Key、LLM、默认模型）补进设置 store —— 只补空值。
 *
 * 为什么要它：sidecar 每次启动都用随机端口，iframe 的 origin 随之变化，而设置只存在
 * localStorage（按 origin 隔离），所以每重启一次设置就等于被清空。这里在每次页面加载时
 * 把运维配置重新补给空字段，用户不必再重输一遍 Key；用户自己改过的字段保持不动。
 * 挂在 root layout，渲染 null。
 */
export function ConfiguredDefaultsInitializer({ defaults }: { defaults: ConfiguredDefaults | null }) {
  useEffect(() => {
    if (!defaults) return;
    const store = useSettingsStore;
    const patch = configuredDefaultsPatch(store.getState(), defaults);
    if (Object.keys(patch).length > 0) store.setState(patch);
  }, [defaults]);

  return null;
}
