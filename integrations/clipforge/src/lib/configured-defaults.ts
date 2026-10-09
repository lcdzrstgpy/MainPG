import type { LLMSetting, ProviderSetting, SettingsState } from "@/lib/stores/settings-store";

/**
 * 运维下发的「默认设置」在前端的落地规则（纯函数，客户端安全）。
 *
 * 背景：sidecar 每次启动都用随机端口，iframe 的 origin 随之变化，而设置只存在
 * localStorage（按 origin 隔离）——于是每重启一次，用户填过的平台 Key 与默认模型全丢，
 * 不得不再输一遍。运维配置（`clipforge.local.json`，经 `WH_CLIPFORGE_CONFIG` 注入路径）
 * 由 root layout 读出后交给本模块，用来把「空着的」字段补上。
 *
 * 语义：**只补空**。用户自己改过或已持久化的值优先，配置只做兜底——否则运维配置会
 * 悄悄覆盖用户在设置页里的修改。
 */
export interface ConfiguredDefaults {
  providers?: Record<string, Partial<ProviderSetting>>;
  llm?: Partial<LLMSetting>;
  defaultImageModel?: string;
  defaultVideoModel?: string;
}

/** 生成需要写回 store 的补丁；没有任何可补的字段时返回空对象。 */
export function configuredDefaultsPatch(
  state: SettingsState,
  defaults: ConfiguredDefaults | null | undefined
): Partial<SettingsState> {
  if (!defaults) return {};
  const patch: Partial<SettingsState> = {};

  const providers = { ...state.providers };
  let providersChanged = false;
  for (const [name, configured] of Object.entries(defaults.providers ?? {})) {
    const apiKey = configured?.apiKey?.trim();
    if (!apiKey) continue;
    const current = state.providers[name];
    // 已有 Key 就不动：配置只负责把「空的」填上
    if (current?.apiKey?.trim()) continue;
    providers[name] = {
      ...current,
      ...configured,
      enabled: configured?.enabled ?? current?.enabled ?? true,
      apiKey,
    };
    providersChanged = true;
  }
  if (providersChanged) patch.providers = providers;

  const llmApiKey = defaults.llm?.apiKey?.trim();
  if (defaults.llm && llmApiKey && !state.llm.apiKey.trim()) {
    patch.llm = { ...state.llm, ...defaults.llm, apiKey: llmApiKey };
  }

  const imageModel = defaults.defaultImageModel?.trim();
  if (imageModel && !state.defaultImageModel.trim()) patch.defaultImageModel = imageModel;

  const videoModel = defaults.defaultVideoModel?.trim();
  if (videoModel && !state.defaultVideoModel.trim()) patch.defaultVideoModel = videoModel;

  return patch;
}
