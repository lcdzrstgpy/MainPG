// @vitest-environment node
/**
 * scriptCharacterFrom：脚本接口 character 载荷的唯一构造器。
 * 页面（脚本页重新生成）与路由测试共用它，避免「页面拼一份、接口再拼一份」。
 */
import { describe, expect, it } from "vitest";
import { scriptCharacterFrom } from "@/lib/script-character";

describe("scriptCharacterFrom", () => {
  it("完整角色 → 只输出脚本接口需要的四个字段（外观 + 声线）", () => {
    expect(
      scriptCharacterFrom({
        id: "char_a",
        name: "小美",
        appearance: "32岁有亲和力，松散低马尾，左手腕套着旧发圈",
        voiceProfile: { style: "轻快女声" },
      })
    ).toEqual({
      id: "char_a",
      name: "小美",
      appearance: "32岁有亲和力，松散低马尾，左手腕套着旧发圈",
      voiceStyle: "轻快女声",
    });
  });

  it("没有声线时不写 voiceStyle 键（保持可选语义）", () => {
    const payload = scriptCharacterFrom({ id: "char_a", name: "小美", appearance: "长发" });
    expect(payload).toEqual({ id: "char_a", name: "小美", appearance: "长发" });
    expect(payload && "voiceStyle" in payload).toBe(false);
  });

  it("appearance 缺失或空串 → 归一为空串（不产生 undefined 字段）", () => {
    expect(scriptCharacterFrom({ id: "char_a", name: "小美" })?.appearance).toBe("");
    expect(scriptCharacterFrom({ id: "char_a", name: "小美", appearance: "" })?.appearance).toBe("");
  });

  it("voiceProfile 为空对象 / style 为空串 → 不写 voiceStyle", () => {
    expect(scriptCharacterFrom({ id: "char_a", name: "小美", voiceProfile: {} })).toEqual({
      id: "char_a",
      name: "小美",
      appearance: "",
    });
    expect(
      scriptCharacterFrom({ id: "char_a", name: "小美", voiceProfile: { style: "" } })
    ).toEqual({ id: "char_a", name: "小美", appearance: "" });
  });

  it("角色不存在（未选 / 角色库查找失败）→ undefined，调用方不得发送 character 键", () => {
    expect(scriptCharacterFrom(undefined)).toBeUndefined();
    expect(scriptCharacterFrom(null)).toBeUndefined();
  });

  it("id 或 name 为空白 → undefined（脏数据不当成角色）", () => {
    expect(scriptCharacterFrom({ id: "  ", name: "小美" })).toBeUndefined();
    expect(scriptCharacterFrom({ id: "char_a", name: "" })).toBeUndefined();
  });

  it("id / name / voiceStyle 两侧空白被裁掉，避免带空格的 id 对不上角色库", () => {
    expect(
      scriptCharacterFrom({ id: "  char_a  ", name: "  小美  ", appearance: "长发", voiceProfile: { style: " 轻快女声 " } })
    ).toEqual({ id: "char_a", name: "小美", appearance: "长发", voiceStyle: "轻快女声" });
  });

  it("不夹带角色库里的其它字段（多传的键被丢弃）", () => {
    const payload = scriptCharacterFrom({
      id: "char_a",
      name: "小美",
      appearance: "长发",
      voiceProfile: { style: "轻快女声" },
      referenceImages: ["/api/files/x.png"],
    } as never);
    expect(Object.keys(payload ?? {}).sort()).toEqual(["appearance", "id", "name", "voiceStyle"]);
  });
});
