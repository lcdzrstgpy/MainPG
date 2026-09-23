/**
 * 脚本接口的 `character`（出镜人物）载荷构造。
 *
 * 页面在「重新生成脚本」时也必须把创建时绑定的主播带上，否则 `/api/llm/script` 收到
 * `character === undefined`，提示词里就没有【出镜人物】、外貌锚点与 `shot.characterId` 约束。
 *
 * 放在这里（无 React / 无 next 依赖）是为了让页面与路由测试共用同一份构造逻辑：
 * 页面只负责用角色库里的完整角色对象调用它，服务端 `sanitizeCharacter` 是第二道防线。
 */

/** 角色库里一条主播记录里脚本接口真正需要的那部分（结构类型，便于单测直接构造）。 */
export interface ScriptCharacterSource {
  id: string;
  name: string;
  appearance?: string;
  voiceProfile?: { style?: string } | null;
}

export interface ScriptCharacterPayload {
  id: string;
  name: string;
  appearance: string;
  voiceStyle?: string;
}

/**
 * 只发角色库里真实存在的字段，不信任 URL 或项目记录里的角色文本。
 * id / name 裁掉两侧空白后为空（角色被删除、脏数据）⇒ 返回 undefined，调用方不得发送 character 键。
 */
export function scriptCharacterFrom(
  character: ScriptCharacterSource | null | undefined
): ScriptCharacterPayload | undefined {
  const id = character?.id?.trim();
  const name = character?.name?.trim();
  if (!id || !name) return undefined;
  const voiceStyle = character?.voiceProfile?.style?.trim();
  return {
    id,
    name,
    appearance: character?.appearance || "",
    ...(voiceStyle ? { voiceStyle } : {}),
  };
}
