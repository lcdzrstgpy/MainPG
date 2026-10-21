import { useRef, useState } from "react";

import { generateCharacterSheet } from "../api/endpoints/characters";
import type { Character } from "../api/types";
import { buildImageOptions, resolveDefaultModelTarget } from "../lib/gen-params";
import { aiVideoMediaUrl } from "../lib/media-url";
import { useCharacterStore } from "../state/characterStore";
import { useSettingsStore } from "../state/settingsStore";

type Notice = { kind: "ok" | "error"; text: string } | null;

const EMPTY_FORM = { name: "", description: "", appearance: "", voiceStyle: "" };

/**
 * 角色库管理：CRUD、设为默认、生成 2×2 四视图定妆照、放大预览与删除定妆照。
 * 定妆照只保留第一张（referenceImages[0]），下游分镜/整片都以它作为人物身份锚点。
 */
export function PresenterManager() {
  const { characters, addCharacter, updateCharacter, removeCharacter, setDefault } = useCharacterStore();
  const { providers, defaultImageModel, customModels, imageParams } = useSettingsStore();

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [generating, setGenerating] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<Notice>(null);
  const [preview, setPreview] = useState<{ characterId: string; name: string; url: string } | null>(null);
  // 防止连续点击同一张卡片重复提交（Set 之外再挡一层同步判断）。
  const generatingRef = useRef(new Set<string>());

  function resetForm() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setFormOpen(false);
  }

  function startEdit(character: Character) {
    setEditingId(character.id);
    setForm({
      name: character.name,
      description: character.description ?? "",
      appearance: character.appearance ?? "",
      voiceStyle: character.voiceProfile?.style ?? "",
    });
    setFormOpen(true);
  }

  function handleSave() {
    const name = form.name.trim();
    if (!name) return;
    const appearance = form.appearance.trim();
    const voiceProfile = form.voiceStyle.trim() ? { style: form.voiceStyle.trim() } : undefined;
    if (editingId) {
      updateCharacter(editingId, {
        name,
        description: form.description.trim() || undefined,
        appearance: appearance || undefined,
        voiceProfile,
      });
      setNotice({ kind: "ok", text: `已更新「${name}」` });
    } else {
      addCharacter({
        id: crypto.randomUUID(),
        name,
        ...(form.description.trim() ? { description: form.description.trim() } : {}),
        ...(appearance ? { appearance } : {}),
        referenceImages: [],
        ...(voiceProfile ? { voiceProfile } : {}),
        isDefault: characters.length === 0,
      });
      setNotice({ kind: "ok", text: `已添加角色「${name}」` });
    }
    resetForm();
  }

  async function generateSheet(character: Character) {
    if (!character.appearance?.trim()) {
      setNotice({ kind: "error", text: "先在角色里填写外观描述，才能生成四视图定妆照。" });
      return;
    }
    if (generatingRef.current.has(character.id)) return;
    generatingRef.current.add(character.id);
    setGenerating(new Set(generatingRef.current));
    setNotice(null);
    try {
      const target = await resolveDefaultModelTarget(providers, defaultImageModel, customModels, "image");
      if (!target) {
        throw new Error("请先在设置中启用生图平台并选择默认生图模型。");
      }
      const result = await generateCharacterSheet({
        appearance: character.appearance,
        name: character.name,
        provider: target.provider,
        model: target.model,
        apiKey: target.apiKey,
        baseUrl: target.baseUrl,
        // 定妆照固定方形 2×2 四视图，与用户的视频画幅偏好无关。
        options: buildImageOptions({ ...imageParams, aspectRatio: "1:1", count: 1 }),
      });
      updateCharacter(character.id, {
        referenceImages: [result.url, ...character.referenceImages.slice(1)],
      });
      setNotice({ kind: "ok", text: `已生成「${character.name}」的定妆照` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "定妆照生成失败" });
    } finally {
      generatingRef.current.delete(character.id);
      setGenerating(new Set(generatingRef.current));
    }
  }

  function deleteSheet(character: Character) {
    updateCharacter(character.id, { referenceImages: character.referenceImages.slice(1) });
    setPreview(null);
    setNotice({ kind: "ok", text: `已删除「${character.name}」的定妆照` });
  }

  function handleDelete(character: Character) {
    if (!window.confirm(`确定删除角色「${character.name}」吗？已创建的视频项目不受影响。`)) return;
    removeCharacter(character.id);
    if (preview?.characterId === character.id) setPreview(null);
  }

  return (
    <div className="ai-video-presenter-manager">
      <p className="ai-video-presenter-intro">
        角色是可复用的出镜人物。填写外观后生成四视图定妆照，后续分镜、整片与角色绑定都会以它锁定人物一致性。
      </p>

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      {characters.length === 0 ? (
        <div className="ai-video-empty">
          <span className="ai-video-empty-icon">♙</span>
          <strong>还没有角色</strong>
          <p>先创建一个出镜人物：名字、简短介绍和外观描述即可。</p>
          <button type="button" onClick={() => setFormOpen(true)}>新建角色</button>
        </div>
      ) : (
        <div className="ai-video-presenter-grid">
          {characters.map((character) => {
            const sheet = aiVideoMediaUrl(character.referenceImages[0] ?? null);
            const busy = generating.has(character.id);
            return (
              <article key={character.id} className={`ai-video-presenter-card${character.isDefault ? " is-default" : ""}`}>
                <div className="ai-video-presenter-head">
                  <button
                    type="button"
                    className="ai-video-presenter-avatar"
                    disabled={!sheet}
                    title={sheet ? "查看定妆照" : "还没有定妆照"}
                    onClick={() => {
                      if (sheet) setPreview({ characterId: character.id, name: character.name, url: sheet });
                    }}
                  >
                    {sheet ? <img src={sheet} alt="" loading="lazy" /> : <span>♙</span>}
                  </button>
                  <div className="ai-video-presenter-info">
                    <strong>{character.name}</strong>
                    {character.isDefault && <em>默认</em>}
                    {character.description && <p>{character.description}</p>}
                    {character.appearance && <span>外观：{character.appearance}</span>}
                    {character.voiceProfile?.style && <span>声音：{character.voiceProfile.style}</span>}
                  </div>
                </div>
                <div className="ai-video-presenter-actions">
                  <button type="button" className="is-primary" disabled={busy} onClick={() => void generateSheet(character)}>
                    {busy ? "生成中…" : sheet ? "重做定妆照" : "生成定妆照"}
                  </button>
                  {!character.isDefault && (
                    <button type="button" onClick={() => setDefault(character.id)}>设为默认</button>
                  )}
                  <button type="button" onClick={() => startEdit(character)}>编辑</button>
                  <button type="button" className="is-danger" onClick={() => handleDelete(character)}>删除</button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {!formOpen && characters.length > 0 && (
        <button type="button" className="ai-video-presenter-add" onClick={() => setFormOpen(true)}>＋ 新建角色</button>
      )}

      {formOpen && (
        <section className="ai-video-form-card" aria-label={editingId ? "编辑角色" : "新建角色"}>
          <div className="ai-video-form-head">
            <h3>{editingId ? "编辑角色" : "新建角色"}</h3>
            <p>外观描述会被注入生图提示词，越具体越稳定（发型、服装、年龄、气质）。</p>
          </div>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-character-name">名字 *</label>
              <input
                id="ai-video-character-name"
                value={form.name}
                placeholder="例如：小美"
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-character-voice">声音风格</label>
              <input
                id="ai-video-character-voice"
                value={form.voiceStyle}
                placeholder="例如：温柔女声 / 专业男声"
                onChange={(event) => setForm((current) => ({ ...current, voiceStyle: event.target.value }))}
              />
            </div>
          </div>
          <div className="ai-video-field">
            <label htmlFor="ai-video-character-description">简介</label>
            <input
              id="ai-video-character-description"
              value={form.description}
              placeholder="例如：25 岁女生，活泼开朗"
              onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
            />
          </div>
          <div className="ai-video-field">
            <label htmlFor="ai-video-character-appearance">外观描述</label>
            <textarea
              id="ai-video-character-appearance"
              rows={3}
              value={form.appearance}
              placeholder="例如：黑色长直发、米色针织衫、25 岁、自然淡妆"
              onChange={(event) => setForm((current) => ({ ...current, appearance: event.target.value }))}
            />
            <span className="ai-video-field-hint">生成定妆照前必须填写；同一角色后续镜头都会沿用它。</span>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" disabled={!form.name.trim()} onClick={handleSave}>
              {editingId ? "保存修改" : "添加角色"}
            </button>
            <button type="button" className="is-secondary" onClick={resetForm}>取消</button>
          </div>
        </section>
      )}

      {preview && (
        <div className="ai-video-modal-backdrop" role="presentation" onClick={() => setPreview(null)}>
          <div
            className="ai-video-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`${preview.name} 的定妆照`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="ai-video-modal-head">
              <h3>{preview.name} 的定妆照</h3>
              <button type="button" aria-label="关闭" onClick={() => setPreview(null)}>×</button>
            </div>
            <img className="ai-video-sheet-preview" src={preview.url} alt={`${preview.name} 定妆照`} />
            <div className="ai-video-settings-actions">
              <button
                type="button"
                className="is-secondary"
                onClick={() => {
                  const character = characters.find((item) => item.id === preview.characterId);
                  if (character) deleteSheet(character);
                }}
              >
                删除定妆照
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
