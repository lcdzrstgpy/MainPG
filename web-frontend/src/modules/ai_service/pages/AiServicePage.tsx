import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent, FormEvent, KeyboardEvent, MouseEvent } from "react";

import { generatedImageDownloadName } from "../data/assetDownload";
import { pastedImageFile, shouldSendOnEnter } from "../data/chatComposerEvents";
import { consumeComposerDraft } from "../data/composerDraft";
import { modelsForMode } from "../data/modelCatalog";
import type { AiConversation, AiCreationMode, AiCreationTemplate, AiMessage, AiPodJob } from "../types";
import { aiServiceApi } from "../api/aiServiceApi";
import "../styles/aiService.css";

const modeLabels: Record<AiCreationMode, string> = {
  chat: "智能对话",
  generate: "文生图",
  edit: "商品改图",
  pod: "POD 出图",
};

const templateIcons = ["icon-image", "icon-camera", "icon-bg-colors", "icon-star"];

function modeIcon(mode: AiCreationMode) {
  if (mode === "pod") return "icon-star";
  if (mode === "edit") return "icon-edit";
  if (mode === "generate") return "icon-image";
  return "icon-comment";
}

export function AiServicePage() {
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [localConversations, setLocalConversations] = useState<AiConversation[]>([]);
  const [runtimeTemplates, setRuntimeTemplates] = useState<AiCreationTemplate[]>([]);
  const [selectedModelId, setSelectedModelId] = useState("deepseek-v4-flash");
  const [mode, setMode] = useState<AiCreationMode>("chat");
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<AiMessage[]>([]);
  const [uploadedImageUrl, setUploadedImageUrl] = useState<string>();
  const [uploadedImageName, setUploadedImageName] = useState("");
  const [uploadedDocumentName, setUploadedDocumentName] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const [remoteConversationId, setRemoteConversationId] = useState<string>();
  const [uploadedAssetId, setUploadedAssetId] = useState<string>();
  const [uploadedDocumentAssetId, setUploadedDocumentAssetId] = useState<string>();
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [apiStatus, setApiStatus] = useState<"loading" | "ready" | "error">("loading");
  const [apiError, setApiError] = useState("");
  const [conversationMenu, setConversationMenu] = useState<{ conversation: AiConversation; x: number; y: number }>();
  const [renamingConversation, setRenamingConversation] = useState<AiConversation>();
  const [renameValue, setRenameValue] = useState("");
  const [aspectRatio, setAspectRatio] = useState("1:1");
  const [sceneStyle, setSceneStyle] = useState("");
  const [podJob, setPodJob] = useState<AiPodJob>();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const documentInputRef = useRef<HTMLInputElement>(null);
  const conversationFlowRef = useRef<HTMLDivElement>(null);
  const imageUploadVersionRef = useRef(0);
  const objectUrlsRef = useRef(new Set<string>());

  const selectableModels = useMemo(() => modelsForMode(mode), [mode]);
  const selectedModel = useMemo(
    () => selectableModels.find((model) => model.id === selectedModelId) ?? selectableModels[0],
    [selectableModels, selectedModelId],
  );

  useEffect(() => {
    if (!selectableModels.some((model) => model.id === selectedModelId)) setSelectedModelId(selectableModels[0].id);
  }, [selectableModels, selectedModelId]);

  useEffect(() => () => {
    objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrlsRef.current.clear();
  }, []);

  useEffect(() => {
    if (!podJob || !["running", "queued"].includes(podJob.status)) return;
    const timer = window.setInterval(() => void refreshPodJob(podJob.creationId), 1000);
    return () => window.clearInterval(timer);
  }, [podJob?.creationId, podJob?.status]);

  useEffect(() => {
    const flow = conversationFlowRef.current;
    if (!flow) return;
    flow.scrollTo({ top: flow.scrollHeight, behavior: "smooth" });
  }, [messages, isGenerating, podJob]);

  useEffect(() => {
    if (!conversationMenu) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setConversationMenu(undefined);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [conversationMenu]);

  useEffect(() => {
    aiServiceApi.bootstrap().then((data) => {
      setRuntimeTemplates(data.templates.map((template, index) => ({
        ...template,
        icon: templateIcons[index % templateIcons.length],
      })));
      setLocalConversations(sortConversations(data.conversations.map(toConversation)));
      setApiStatus("ready");
    }).catch((error) => {
      setApiStatus("error");
      setApiError(error instanceof Error ? error.message : "本机 AI 服务未连接");
    });
  }, []);

  const selectTemplate = (template: AiCreationTemplate) => {
    setPrompt(template.prompt);
    setMode(template.mode);
  };

  const clearComposerAttachment = (revokePreview = false) => {
    imageUploadVersionRef.current += 1;
    setIsUploadingImage(false);
    if (revokePreview && uploadedImageUrl) {
      URL.revokeObjectURL(uploadedImageUrl);
      objectUrlsRef.current.delete(uploadedImageUrl);
    }
    setUploadedImageUrl(undefined);
    setUploadedImageName("");
    setUploadedAssetId(undefined);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const clearComposerDocument = () => {
    setUploadedDocumentName("");
    setUploadedDocumentAssetId(undefined);
    if (documentInputRef.current) documentInputRef.current.value = "";
  };

  const selectImage = async (file?: File) => {
    if (!file || !file.type.startsWith("image/")) return;
    const uploadVersion = imageUploadVersionRef.current + 1;
    imageUploadVersionRef.current = uploadVersion;
    if (uploadedImageUrl) {
      URL.revokeObjectURL(uploadedImageUrl);
      objectUrlsRef.current.delete(uploadedImageUrl);
    }
    const imageUrl = URL.createObjectURL(file);
    objectUrlsRef.current.add(imageUrl);
    setUploadedImageUrl(imageUrl);
    setUploadedImageName(file.name);
    setUploadedAssetId(undefined);
    if (apiStatus !== "ready") return;
    setIsUploadingImage(true);
    try {
      const asset = await aiServiceApi.uploadAsset(file);
      if (imageUploadVersionRef.current !== uploadVersion) return;
      setUploadedAssetId(asset.asset_id);
    } catch (error) {
      if (imageUploadVersionRef.current !== uploadVersion) return;
      setApiError(error instanceof Error ? error.message : "图片上传失败，已保留本地预览");
    } finally {
      if (imageUploadVersionRef.current === uploadVersion) setIsUploadingImage(false);
    }
  };

  const selectDocument = async (file?: File) => {
    if (!file || !/\.(txt|csv|xlsx|docx)$/i.test(file.name)) {
      setApiError("文件仅支持 TXT、CSV、XLSX 或 DOCX。");
      return;
    }
    setUploadedDocumentName(file.name);
    setUploadedDocumentAssetId(undefined);
    if (apiStatus !== "ready") return;
    try {
      const asset = await aiServiceApi.uploadAsset(file);
      setUploadedDocumentAssetId(asset.asset_id);
    } catch (error) {
      clearComposerDocument();
      setApiError(error instanceof Error ? error.message : "文件上传或本地解析失败");
    }
  };

  const openNewCreation = () => {
    setActiveConversationId(undefined);
    setMessages([]);
    setPrompt("");
    setApiError("");
    clearComposerAttachment(true);
    clearComposerDocument();
    setWebSearchEnabled(false);
    setRemoteConversationId(undefined);
    setPodJob(undefined);
  };

  const handleModeChange = async (nextMode: AiCreationMode) => {
    setMode(nextMode);
    const recent = recentConversationForMode(localConversations, nextMode);
    if (recent) await selectConversation(recent);
    else openNewCreation();
  };

  const updateLocalConversation = (updated: { conversation_id: string; title: string; mode: AiCreationMode; is_pinned: boolean; updated_at: string }) => {
    setLocalConversations((current) => sortConversations(current.map((conversation) => (
      conversation.id === updated.conversation_id ? toConversation(updated) : conversation
    ))));
  };

  const openConversationMenu = (event: MouseEvent<HTMLButtonElement>, conversation: AiConversation) => {
    event.preventDefault();
    setConversationMenu({
      conversation,
      x: Math.min(event.clientX, window.innerWidth - 180),
      y: Math.min(event.clientY, window.innerHeight - 150),
    });
  };

  const beginRenameConversation = (conversation: AiConversation) => {
    setConversationMenu(undefined);
    setRenamingConversation(conversation);
    setRenameValue(conversation.title);
  };

  const saveConversationRename = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!renamingConversation || !renameValue.trim()) return;
    try {
      const updated = await aiServiceApi.updateConversation(renamingConversation.id, { title: renameValue.trim() });
      updateLocalConversation(updated);
      setRenamingConversation(undefined);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "会话重命名失败");
    }
  };

  const toggleConversationPin = async (conversation: AiConversation) => {
    setConversationMenu(undefined);
    try {
      updateLocalConversation(await aiServiceApi.updateConversation(conversation.id, { is_pinned: !conversation.isPinned }));
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "会话置顶失败");
    }
  };

  const removeConversation = async (conversation: AiConversation) => {
    setConversationMenu(undefined);
    if (!window.confirm(`确定删除“${conversation.title}”？该会话及附件将从本机移除。`)) return;
    try {
      await aiServiceApi.deleteConversation(conversation.id);
      setLocalConversations((current) => current.filter((item) => item.id !== conversation.id));
      if (activeConversationId === conversation.id) openNewCreation();
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "删除会话失败");
    }
  };

  const selectConversation = async (conversation: AiConversation) => {
    setActiveConversationId(conversation.id);
    setRemoteConversationId(conversation.id);
    setApiError("");
    try {
      const history = await aiServiceApi.messages(conversation.id);
      const restored = await Promise.all(history.messages.map(async (message) => ({
        id: message.message_id,
        role: message.role,
        content: message.content,
        generatedImageUrls: message.role === "assistant" && message.asset_ids.length
          ? await Promise.all(message.asset_ids.map(aiServiceApi.loadAssetUrl)) : undefined,
      })));
      setMessages(restored);
      setPrompt("");
      const latestPod = await aiServiceApi.latestPodCreation(conversation.id);
      if (latestPod.creation_id) await applyPodStatus(latestPod);
      else setPodJob(undefined);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "本地会话加载失败");
    }
  };

  const refreshPodJob = async (creationId: string) => {
    try {
      const status = await aiServiceApi.podCreationStatus(creationId);
      await applyPodStatus(status);
      if (!["running", "queued"].includes(status.status)) setIsGenerating(false);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "POD 任务状态读取失败");
    }
  };

  const applyPodStatus = async (status: Awaited<ReturnType<typeof aiServiceApi.podCreationStatus>>) => {
    if (!status.creation_id) return;
    const groups = await Promise.all(status.groups.map(async (group) => ({
      groupId: group.group_id,
      kind: group.kind,
      label: group.label,
      status: group.status,
      imageUrls: await Promise.all(group.asset_ids.map(aiServiceApi.loadAssetUrl)),
      errorMessage: group.error_message,
    })));
    setPodJob({ creationId: status.creation_id, conversationId: status.conversation_id, createdAt: status.created_at, status: status.status, groups });
  };

  const retryPodGroup = async (kind: string) => {
    if (!podJob) return;
    try {
      await aiServiceApi.retryPodGroup(podJob.creationId, kind);
      setIsGenerating(true);
      await refreshPodJob(podJob.creationId);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "重试失败，请稍后再试");
    }
  };

  const handleChatPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (mode !== "chat") return;
    const image = pastedImageFile(event.clipboardData.items);
    if (!image) return;
    event.preventDefault();
    if (!selectedModel.acceptsImageInput) return;
    void selectImage(image);
  };

  const handleChatKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mode !== "chat" || !shouldSendOnEnter(event.nativeEvent)) return;
    event.preventDefault();
    void generate();
  };

  const generate = async () => {
    const content = prompt.trim();
    if (!content || isGenerating || isUploadingImage) return;
    if (apiStatus !== "ready") {
      setApiError("本机 AI 服务尚未连接，无法发送创作请求。");
      return;
    }
    const title = content.length > 18 ? `${content.slice(0, 18)}…` : content;
    const draft = consumeComposerDraft({
      prompt: content,
      imageUrl: uploadedImageUrl,
      imageName: uploadedImageName,
      assetId: uploadedAssetId,
    });
    const documentName = mode === "chat" ? uploadedDocumentName : "";
    const documentAssetId = mode === "chat" ? uploadedDocumentAssetId : undefined;
    const userMessage: AiMessage = { id: `user-${Date.now()}`, role: "user", content: draft.submitted.prompt, ...(draft.submitted.imageUrl ? { uploadedImageUrl: draft.submitted.imageUrl } : {}), ...(documentName ? { uploadedDocumentName: documentName } : {}) };
    setMessages((current) => [...current, userMessage]);
    setPrompt(draft.next.prompt);
    setUploadedImageUrl(draft.next.imageUrl);
    setUploadedImageName(draft.next.imageName);
    setUploadedAssetId(draft.next.assetId);
    if (fileInputRef.current) fileInputRef.current.value = "";
    clearComposerDocument();
    setIsGenerating(true);
    setApiError("");
    try {
      const conversation = remoteConversationId
        ? { conversation_id: remoteConversationId }
        : await aiServiceApi.createConversation(title, mode);
      setRemoteConversationId(conversation.conversation_id);
      setActiveConversationId(conversation.conversation_id);
      setLocalConversations((current) => {
        const updatedAt = new Date().toISOString();
        const existing = current.find((item) => item.id === conversation.conversation_id);
        return sortConversations([
          existing
            ? { ...existing, preview: modeLabels[mode], time: "刚刚", updatedAt }
            : { id: conversation.conversation_id, title, mode, preview: modeLabels[mode], time: "刚刚", isPinned: false, updatedAt },
          ...current.filter((item) => item.id !== conversation.conversation_id),
        ]);
      });
      if (mode === "chat") {
        const reply = await aiServiceApi.streamChat({
          conversation_id: conversation.conversation_id,
          content,
          model_id: selectedModel.id,
          asset_ids: [draft.submitted.assetId, documentAssetId].filter((assetId): assetId is string => Boolean(assetId)),
          web_search: webSearchEnabled,
        });
        setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", content: reply || "模型未返回文字内容。" }]);
      } else if (mode === "pod") {
        const result = await aiServiceApi.createPodImages({
          conversation_id: conversation.conversation_id,
          prompt: content,
          asset_ids: draft.submitted.assetId ? [draft.submitted.assetId] : [],
        });
        await refreshPodJob(result.creation_id);
      } else {
        const template = runtimeTemplates.find((item) => item.mode === mode);
        if (!template) throw new Error("创作模板仍在加载，请稍后重试。");
        const result = await aiServiceApi.createImage({
          conversation_id: conversation.conversation_id,
          template_id: template.id,
          model_id: selectedModel.id,
          prompt: `${content}${sceneStyle ? `\n场景风格：${sceneStyle}` : ""}`,
          size: imageSizeFor(aspectRatio),
          asset_ids: draft.submitted.assetId ? [draft.submitted.assetId] : [],
        });
        const generatedImageUrls = await Promise.all(result.asset_ids.map(aiServiceApi.loadAssetUrl));
        setMessages((current) => [...current, {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: `已生成 ${result.asset_ids.length} 张商品创作图。`,
          generatedImageUrls,
        }]);
      }
    } catch (error) {
      setApiError(mode === "chat" && draft.submitted.assetId
        ? "模型请求失败，请切换模型后重试。"
        : error instanceof Error ? error.message : "创作任务失败");
    } finally {
      if (mode !== "pod") setIsGenerating(false);
    }
  };

  return (
    <section className="ai-service-page" aria-label="AI 服务">
      <aside className="ai-service-sessions">
        <div className="ai-service-brand"><span className="iconfont icon-robot-fill" aria-hidden="true" /><div><b>AI 服务</b><span>本地商品创作台</span></div></div>
        <button className="ai-new-creation" type="button" onClick={openNewCreation}><span className="iconfont icon-plus" aria-hidden="true" /> 新建创作</button>
        <div className="ai-session-section">
          <div className="ai-section-heading"><span>创作模板</span></div>
          <div className="ai-template-list">
            {runtimeTemplates.map((template) => <button key={template.id} type="button" className="ai-template" onClick={() => selectTemplate(template)}><span className={`iconfont ${template.icon}`} aria-hidden="true" /><span><b>{template.label}</b><small>{template.description}</small></span></button>)}
            {apiStatus === "loading" && <small className="ai-side-loading">正在加载模板…</small>}
          </div>
        </div>
        <div className="ai-session-section ai-history-section">
          <div className="ai-section-heading"><span>本地会话</span><small>仅此设备</small></div>
          <div className="ai-history-list">
            {localConversations.map((conversation) => <button type="button" key={conversation.id} className={`ai-history-item ${activeConversationId === conversation.id ? "is-active" : ""}`} onClick={() => void selectConversation(conversation)} onContextMenu={(event) => openConversationMenu(event, conversation)}><span className="iconfont icon-comment" aria-hidden="true" /><span><b>{conversation.isPinned && <i className="ai-history-pin" aria-label="已置顶">置顶</i>} {conversation.title}</b><small>{conversation.preview}</small></span><time>{conversation.time}</time></button>)}
          </div>
        </div>
        <p className="ai-local-note"><span className="iconfont icon-safetycertificate" aria-hidden="true" /> 会话与附件仅保存到本机</p>
      </aside>

      <main className="ai-service-workspace">
        <header className="ai-workspace-header"><div><p>AI SERVICE / CREATIVE STUDIO</p><h1>商品创作</h1></div><div className="ai-header-status"><span className="ai-status-dot" /> {apiStatus === "ready" ? "本机服务已连接" : apiStatus === "loading" ? "正在连接本机服务" : "本机服务未连接"}</div></header>
        <div className="ai-mode-tabs" role="tablist" aria-label="创作模式">
          {(["chat", "generate", "edit", "pod"] as AiCreationMode[]).map((item) => <button key={item} type="button" className={mode === item ? "is-active" : ""} onClick={() => void handleModeChange(item)} role="tab" aria-selected={mode === item}><span className={`iconfont ${modeIcon(item)}`} aria-hidden="true" /> {modeLabels[item]}</button>)}
        </div>
        <div ref={conversationFlowRef} className={`ai-conversation-flow ${messages.length ? "has-messages" : ""}`}>
          {!messages.length && <EmptyCreationState mode={mode} />}
          {messages.map((message) => <article key={message.id} className={`ai-message ai-message-${message.role}`}><span className={`ai-message-avatar iconfont ${message.role === "assistant" ? "icon-robot-fill" : "icon-user"}`} aria-hidden="true" /><div className="ai-message-body"><p>{message.content}</p>{message.uploadedImageUrl && <img className="ai-uploaded-in-message" src={message.uploadedImageUrl} alt="用户上传图片" />}{message.uploadedDocumentName && <div className="ai-document-in-message"><span className="iconfont icon-file" aria-hidden="true" />已附本地资料：{message.uploadedDocumentName}</div>}{message.generatedImageGroups && <GeneratedAssetCards groups={message.generatedImageGroups} />}{message.generatedImageUrls && <GeneratedAssetCards imageUrls={message.generatedImageUrls} />}</div></article>)}
          {podJob && <PodJobCard job={podJob} onRetry={retryPodGroup} />}
          {isGenerating && mode !== "pod" && <article className="ai-message ai-message-assistant"><span className="ai-message-avatar iconfont icon-robot-fill" /><div className="ai-generating"><span /><span /><span /> 正在生成…</div></article>}
        </div>
        <div className="ai-composer-wrap">
          {uploadedImageUrl && <div className="ai-upload-preview"><img src={uploadedImageUrl} alt="待发送图片" /><div><b>{uploadedImageName}</b><span>{uploadedAssetId ? mode === "chat" ? "已保存到本机，将作为看图资料发送" : "已保存到本机，将作为商品主体参考图" : "正在保存到本机"}</span></div><button type="button" onClick={() => clearComposerAttachment(true)} aria-label="移除图片">×</button></div>}
          {mode === "chat" && uploadedDocumentName && <div className="ai-upload-preview ai-document-preview"><span className="iconfont icon-file" aria-hidden="true" /><div><b>{uploadedDocumentName}</b><span>{uploadedDocumentAssetId ? "已在本机解析，将作为资料发送" : "正在解析本地资料"}</span></div><button type="button" onClick={clearComposerDocument} aria-label="移除文件">×</button></div>}
          <div className="ai-composer"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onPaste={handleChatPaste} onKeyDown={handleChatKeyDown} placeholder={mode === "chat" ? "输入问题；可附图片、资料文件或开启联网搜索…" : mode === "edit" ? "上传商品图，并描述想替换的背景或场景…" : mode === "pod" ? "描述商品、目标市场、卖点或尺寸；可附一张商品 / 设计参考图…" : "描述你想要创作的商品视觉…"} /><div className="ai-composer-actions"><div>{(mode !== "chat" || selectedModel.acceptsImageInput) && <><input ref={fileInputRef} type="file" accept="image/*" hidden onChange={(event) => void selectImage(event.target.files?.[0])} /><button type="button" className="ai-attach-button" onClick={() => fileInputRef.current?.click()}><span className="iconfont icon-image" /> 图片</button></>}{mode === "chat" && <><input ref={documentInputRef} type="file" accept=".txt,.csv,.xlsx,.docx" hidden onChange={(event) => void selectDocument(event.target.files?.[0])} /><button type="button" className="ai-attach-button" onClick={() => documentInputRef.current?.click()}><span className="iconfont icon-file" /> 文件</button><button type="button" className={`ai-search-toggle ${webSearchEnabled ? "is-on" : ""}`} onClick={() => setWebSearchEnabled((value) => !value)} title="联网搜索公开资料"><span className="iconfont icon-search" />联网搜索</button></>}<span className="ai-upload-hint">{mode === "chat" ? selectedModel.acceptsImageInput ? "图片与资料文件可同时附上" : "当前模型仅支持文字与资料文件" : mode === "pod" ? "可单独输入文字，也可附商品或设计图" : "上传商品图后开始创作"}</span></div><button type="button" className="ai-generate-button" disabled={!prompt.trim() || isGenerating || isUploadingImage || apiStatus !== "ready"} onClick={() => void generate()}>{mode === "chat" ? "发送" : mode === "pod" ? "生成 6 图" : "开始创作"}<span className="iconfont icon-arrowright" /></button></div></div>
        </div>
        {apiError && <p className="ai-api-error" role="alert">{apiError}</p>}
      </main>

      <aside className="ai-creation-settings" aria-label="创作参数">
        <section className="ai-settings-card ai-model-card"><span className="ai-card-kicker">MODEL</span><h2>{mode === "chat" ? "对话模型" : mode === "pod" ? "POD 固定模型" : "图片模型"}</h2><label>模型选择<select value={selectedModel.id} disabled={mode === "pod"} onChange={(event) => setSelectedModelId(event.target.value)}>{selectableModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label><p>{mode === "pod" ? "固定使用 GPT Image 2 · 1K，统一输出供应商图片包。" : selectedModel.description}</p><div className="ai-capability-tags"><span>{modeLabels[mode]}</span></div></section>
        {mode === "pod" ? <section className="ai-settings-card ai-pod-delivery-card"><span className="ai-card-kicker">POD DELIVERY</span><h2>供应商交付图</h2><p>场景图 2 张 · 功能图 2 张 · 尺寸图 1 张 · 白底图 1 张</p><small>统一 1:1 · 1024px；尺寸信息请直接写入输入框。</small></section> : mode !== "chat" && <section className="ai-settings-card"><span className="ai-card-kicker">CREATION SETTINGS</span><h2>创作参数</h2><label>画面比例<select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option>1:1</option><option>4:5</option><option>3:4</option><option>16:9</option></select></label><label>场景风格 <small>可选</small><input value={sceneStyle} onChange={(event) => setSceneStyle(event.target.value)} list="ai-scene-style-suggestions" placeholder="如：新中式茶室、法式复古花园" /><datalist id="ai-scene-style-suggestions"><option value="自然家居" /><option value="纯色影棚" /><option value="轻奢生活方式" /><option value="户外通勤" /><option value="新中式茶室" /><option value="节日促销陈列" /></datalist></label></section>}
        <section className="ai-settings-card ai-tips-card"><span className="iconfont icon-bulb-fill" /><div><b>商品图创作小贴士</b><p>上传正面、清晰且无遮挡的商品图，换背景和场景图的效果会更稳定。</p></div></section>
      </aside>
      {conversationMenu && <>
        <button className="ai-context-backdrop" type="button" aria-label="关闭会话菜单" onClick={() => setConversationMenu(undefined)} />
        <div className="ai-conversation-menu" role="menu" style={{ left: conversationMenu.x, top: conversationMenu.y }}>
          <button type="button" role="menuitem" onClick={() => beginRenameConversation(conversationMenu.conversation)}>重命名</button>
          <button type="button" role="menuitem" onClick={() => void toggleConversationPin(conversationMenu.conversation)}>{conversationMenu.conversation.isPinned ? "取消置顶" : "置顶"}</button>
          <button type="button" role="menuitem" className="is-danger" onClick={() => void removeConversation(conversationMenu.conversation)}>删除会话</button>
        </div>
      </>}
      {renamingConversation && <div className="ai-dialog-backdrop" role="presentation"><form className="ai-conversation-dialog" onSubmit={(event) => void saveConversationRename(event)}><h2>重命名会话</h2><input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} maxLength={80} aria-label="会话名称" /><div><button type="button" onClick={() => setRenamingConversation(undefined)}>取消</button><button type="submit" disabled={!renameValue.trim()}>保存</button></div></form></div>}
    </section>
  );
}

function EmptyCreationState({ mode }: { mode: AiCreationMode }) {
  const description = mode === "chat" ? "选择模型后，直接输入商品创作问题。" : mode === "edit" ? "上传商品图，再描述你想替换的背景或场景。" : mode === "pod" ? "输入商品信息或上传参考图，一次输出供应商可用的 6 张商品图。" : "描述商品和想要的画面，即可开始生成。";
  return <div className="ai-empty-creation"><span className={`iconfont ${modeIcon(mode)}`} aria-hidden="true" /><h2>{modeLabels[mode]}</h2><p>{description}</p></div>;
}

function GeneratedAssetCards({ imageUrls, groups }: { imageUrls?: string[]; groups?: Array<{ label: string; imageUrls: string[] }> }) {
  if (groups) return <div className="ai-pod-output-groups" aria-label="POD 供应商出图">{groups.map((group) => <section key={group.label}><h3>{group.label}</h3><GeneratedAssetCards imageUrls={group.imageUrls} /></section>)}</div>;
  return <div className="ai-generated-grid ai-generated-real" aria-label="生成图片">{(imageUrls ?? []).map((imageUrl, index) => <article key={imageUrl} className="ai-generated-card"><a href={imageUrl} target="_blank" rel="noreferrer" title="查看原图"><img src={imageUrl} alt={`AI 生成商品图 ${index + 1}`} /></a><a className="ai-download-button" href={imageUrl} download={generatedImageDownloadName(index)}><span className="iconfont icon-download" aria-hidden="true" />下载图片</a></article>)}</div>;
}

function PodJobCard({ job, onRetry }: { job: AiPodJob; onRetry: (kind: string) => void }) {
  const settled = job.groups.filter((group) => group.status === "succeeded").length;
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(job.createdAt).getTime()) / 1000));
  return <article className="ai-message ai-message-assistant ai-pod-job"><span className="ai-message-avatar iconfont icon-robot-fill" /><div className="ai-message-body"><div className="ai-pod-job-heading"><b>POD 供应商出图</b><span>已完成 {settled}/4 组 · 已等待 {formatElapsed(seconds)}</span></div><div className="ai-pod-job-grid">{job.groups.map((group) => <section key={group.groupId} className={`ai-pod-job-group is-${group.status}`}><header><b>{group.label}</b><span>{podGroupStatusLabel(group.status)}</span></header>{group.imageUrls.length > 0 && <GeneratedAssetCards imageUrls={group.imageUrls} />}{["failed", "interrupted"].includes(group.status) && <div className="ai-pod-job-error"><small>{group.errorMessage || "任务已中断"}</small><button type="button" onClick={() => onRetry(group.kind)}>重试此组</button></div>}{["queued", "running"].includes(group.status) && <div className="ai-pod-job-progress"><i /><i /><i /> {group.status === "queued" ? "等待任务启动" : "正在生成"}</div>}</section>)}</div></div></article>;
}

function podGroupStatusLabel(status: AiPodJob["groups"][number]["status"]) {
  return { queued: "等待中", running: "生成中", succeeded: "已完成", failed: "失败", interrupted: "已中断" }[status];
}

function formatElapsed(seconds: number) {
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function imageSizeFor(ratio: string) {
  if (ratio === "1:1") return "1024x1024";
  return ratio === "16:9" ? "1536x1024" : "1024x1536";
}

function toConversation(conversation: { conversation_id: string; title: string; mode: AiCreationMode; is_pinned: boolean; updated_at: string }): AiConversation {
  return { id: conversation.conversation_id, title: conversation.title, mode: conversation.mode, preview: "本地会话", time: new Date(conversation.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }), isPinned: conversation.is_pinned, updatedAt: conversation.updated_at };
}

function sortConversations(conversations: AiConversation[]) {
  return [...conversations].sort((left, right) => Number(right.isPinned) - Number(left.isPinned) || right.updatedAt.localeCompare(left.updatedAt));
}

function recentConversationForMode(conversations: AiConversation[], mode: AiCreationMode) {
  return conversations.filter((conversation) => conversation.mode === mode)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0];
}
