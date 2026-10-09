/**
 * AI Provider 工厂和注册中心
 * 统一管理所有已注册的 AI 平台 Provider
 */

import type { AIProvider, ProviderConfig, ProviderRegistration } from './types'
import { VolcEngineProvider } from './volcengine'
import { SuchuangProvider } from './suchuang'

// ==================== Provider 注册表 ====================

/** 已注册的 Provider 列表 */
const providerRegistry: Map<string, ProviderRegistration> = new Map()

/**
 * 注册一个 Provider
 * @param registration Provider 注册信息
 */
function registerProvider(registration: ProviderRegistration): void {
  providerRegistry.set(registration.name, registration)
}

// MainPG ships only the two approved media platforms. The vendored upstream
// adapters remain in source for AGPL provenance, but are deliberately not
// registered and therefore cannot be selected or called by this module.
registerProvider({
  name: 'volcengine',
  displayName: '火山引擎',
  description: '字节跳动火山引擎，支持可灵（Kling）和豆包 Seedance 等模型',
  factory: (config) => new VolcEngineProvider(config),
})
registerProvider({
  name: 'suchuang',
  displayName: '速创',
  description: '速创 AI 模型平台，支持图片和视频生成',
  factory: (config) => new SuchuangProvider(config),
})

// ==================== 工厂函数 ====================

/**
 * 创建 Provider 实例
 * @param config Provider 配置，必须包含 name 字段
 * @returns AI Provider 实例
 * @throws 如果指定的 Provider 不存在
 *
 * @example
 * ```ts
 * const provider = createProvider({
 *   name: 'volcengine',
 *   apiKey: 'your-api-key',
 *   baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
 * })
 *
 * const result = await provider.generateImage({
 *   modelId: 'doubao-seedream-4-5-251128',
 *   mode: 'text-to-image',
 *   prompt: '一个可爱的猫咪',
 * })
 * ```
 */
export function createProvider(config: ProviderConfig): AIProvider {
  const registration = providerRegistry.get(config.name)

  if (!registration) {
    const available = Array.from(providerRegistry.keys()).join(', ')
    throw new Error(
      `未找到名为 "${config.name}" 的 Provider。可用的 Provider: ${available}`
    )
  }

  return registration.factory(config)
}

/**
 * 获取所有已注册的 Provider 信息
 * @returns Provider 注册信息列表
 */
export function getAvailableProviders(): Array<{
  name: string
  displayName: string
  description: string
}> {
  return Array.from(providerRegistry.values()).map((reg) => ({
    name: reg.name,
    displayName: reg.displayName,
    description: reg.description,
  }))
}

/**
 * 动态注册自定义 Provider
 * @param registration Provider 注册信息
 */
export function registerCustomProvider(registration: ProviderRegistration): void {
  registerProvider(registration)
}

// ==================== 导出类型和类 ====================

export type {
  AIProvider,
  ProviderConfig,
  ProviderRegistration,
  ImageOptions,
  ImageResult,
  VideoOptions,
  VideoResult,
  TaskStatus,
  TaskStatusEnum,
  Model,
  MediaType,
  GenerationMode,
} from './types'

export { BaseProvider, ProviderError } from './base'
export { VolcEngineProvider } from './volcengine'
export { SuchuangProvider } from './suchuang'
