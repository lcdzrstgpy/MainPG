import { useRef, useState } from "react";

import { analyzeReplicateVideo } from "../api/endpoints/replicate";
import { uploadImages } from "../api/endpoints/upload";
import { snapTargetDuration } from "../lib/creation-entry-prefill";
import { writeCloneHandoff } from "../lib/clone-handoff";

const MAX_CLONE_IMAGES = 5;
const MAX_REFERENCE_VIDEO_BYTES = 80 * 1024 * 1024;
/** 复刻用显式风格：中性、非痛点种草，避免主入口再弹风格选择。 */
const CLONE_SCRIPT_STYLE = "scenario";

type StoryboardCard = {
  id: number;
  title: string;
  description: string;
  duration: string;
};

type ProductImage = { id: string; file: File; previewUrl: string };

type CloneAnalysis = {
  duration: number;
  referenceStructure: string;
  shots: Array<{ index: number; duration: number }>;
};

/** 通用高转化结构：没有可解析的视频文件时使用，并在界面上明确标注为「参考」。 */
const GENERIC_STRUCTURE: StoryboardCard[] = [
  { id: 1, title: "开场吸引", description: "产品特写 + 痛点提问，黄金3秒抓住注意力", duration: "0-3s" },
  { id: 2, title: "痛点放大", description: "展示使用前的痛点场景，引起共鸣", duration: "3-8s" },
  { id: 3, title: "产品展示", description: "多角度展示外观、包装与核心卖点", duration: "8-15s" },
  { id: 4, title: "使用演示", description: "真实场景使用演示，突出效果与便捷", duration: "15-25s" },
  { id: 5, title: "效果对比", description: "使用前后效果对比，增强说服力", duration: "25-35s" },
  { id: 6, title: "促销转化", description: "限时优惠 + 购买引导，促成下单", duration: "35-40s" },
];

type ClonePageProps = {
  onNavigate: (path: string) => void;
};

/**
 * 爆款复刻：载入参考节奏 → 确认商品信息 → 产出预填简报交给 /start。
 * 本页不创建项目、不发脚本请求，避免出现第二条创建链路。
 */
export function ClonePage({ onNavigate }: ClonePageProps) {
  const [videoUrl, setVideoUrl] = useState("");
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<CloneAnalysis | null>(null);
  const [storyboards, setStoryboards] = useState<StoryboardCard[]>([]);
  const [analyzeError, setAnalyzeError] = useState("");

  const [images, setImages] = useState<ProductImage[]>([]);
  const [productName, setProductName] = useState("");
  const [sellingPoints, setSellingPoints] = useState("");

  const [handingOff, setHandingOff] = useState(false);
  const [handoffError, setHandoffError] = useState("");

  const referenceInputRef = useRef<HTMLInputElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);

  async function handleAnalyze() {
    if (analyzing || (!videoUrl.trim() && !referenceFile)) return;
    setAnalyzing(true);
    setStoryboards([]);
    setAnalysis(null);
    setAnalyzeError("");
    try {
      if (referenceFile) {
        if (referenceFile.size > MAX_REFERENCE_VIDEO_BYTES) {
          throw new Error("参考视频超过 80MB，请压缩或换一个更短的片段。");
        }
        const data = await analyzeReplicateVideo(referenceFile);
        setAnalysis(data);
        setStoryboards(
          data.shots.map((shot) => ({
            id: shot.index,
            title: `第 ${shot.index} 镜`,
            description: "节奏复刻位：时长与切换节奏跟随参考，内容由 AI 结合你的商品重写",
            duration: `${shot.duration}s`,
          })),
        );
        return;
      }
      // 只有链接时无法下载平台页面：使用通用高转化结构，并在界面上如实标注。
      setStoryboards(GENERIC_STRUCTURE);
    } catch (cause) {
      setAnalyzeError(cause instanceof Error ? cause.message : "参考视频分析失败，请重试");
    } finally {
      setAnalyzing(false);
    }
  }

  function addImages(files: FileList | null) {
    if (!files) return;
    const remaining = MAX_CLONE_IMAGES - images.length;
    if (remaining <= 0) return;
    const added = Array.from(files)
      .slice(0, remaining)
      .filter((file) => file.type.startsWith("image/"))
      .map((file) => ({ id: crypto.randomUUID(), file, previewUrl: URL.createObjectURL(file) }));
    if (added.length) setImages((current) => [...current, ...added]);
  }

  function removeImage(id: string) {
    setImages((current) => {
      const target = current.find((image) => image.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return current.filter((image) => image.id !== id);
    });
  }

  const analyzed = storyboards.length > 0;
  const canHandoff =
    analyzed &&
    images.length > 0 &&
    productName.trim().length > 0 &&
    sellingPoints.trim().length > 0 &&
    !handingOff;

  async function handleHandoff() {
    if (!canHandoff) return;
    setHandingOff(true);
    setHandoffError("");
    try {
      // 商品图先按临时项目 id 落盘，把服务端路径交给主入口（File 跨页面传不过去）。
      const temporaryProjectId = `clone-prefill-${crypto.randomUUID()}`;
      const uploaded = await uploadImages(temporaryProjectId, images.map((image) => image.file));
      const productImages = uploaded.paths ?? [];
      if (productImages.length === 0) {
        throw new Error("商品图暂存失败，请重试后再进入工作台。");
      }
      writeCloneHandoff(window.localStorage, {
        version: 1,
        productName: productName.trim(),
        sellingPoints: sellingPoints.trim(),
        brief: {
          inputMode: "clone",
          styleType: CLONE_SCRIPT_STYLE,
          styleSource: "explicit",
          // 统一合同只有 15/30/60 三档：参考视频时长按最近档位归位。
          targetDuration: snapTargetDuration(analysis?.duration ?? 40),
        },
        ...(analysis?.referenceStructure ? { referenceStructure: analysis.referenceStructure } : {}),
        ...(videoUrl.trim() ? { referenceVideoUrl: videoUrl.trim() } : {}),
        productImages,
      });
      onNavigate("/start");
    } catch (cause) {
      setHandoffError(cause instanceof Error ? cause.message : "交接失败，请重试");
    } finally {
      setHandingOff(false);
    }
  }

  return (
    <section className="ai-video-page" aria-label="爆款复刻">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>爆款复刻</h2>
          <p>贴爆款链接或上传参考视频，载入镜头节奏；确认商品后进入视频工作台生成脚本与成片。</p>
        </div>
        <span className="ai-video-service-badge is-warning">● 只产创作简报，不直接建项目</span>
      </header>

      <section className="ai-video-form-card" aria-label="输入爆款视频">
        <div className="ai-video-form-head">
          <h3>1. 输入爆款视频</h3>
          <p>上传视频文件可真实解析镜头切点；只有链接时使用通用高转化结构作为参考。</p>
        </div>
        <div className="ai-video-link-row">
          <input
            type="url"
            value={videoUrl}
            placeholder="粘贴抖音 / 快手 / 小红书视频链接"
            onChange={(event) => setVideoUrl(event.target.value)}
          />
          <button
            type="button"
            className="is-secondary"
            disabled={analyzing}
            onClick={() => referenceInputRef.current?.click()}
          >
            {referenceFile ? "换一个视频" : "选择视频文件"}
          </button>
        </div>
        <input
          ref={referenceInputRef}
          type="file"
          accept="video/mp4,video/webm,video/quicktime,video/x-m4v"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            setReferenceFile(file);
            event.target.value = "";
          }}
        />
        <div className="ai-video-settings-actions">
          <button type="button" disabled={analyzing || (!videoUrl.trim() && !referenceFile)} onClick={() => void handleAnalyze()}>
            {analyzing ? "载入中…" : "载入结构参考"}
          </button>
          {referenceFile && <span className="ai-video-settings-message is-ok">已选择：{referenceFile.name}</span>}
          {analyzeError && <span className="ai-video-settings-message is-error">{analyzeError}</span>}
        </div>
        <p className="ai-video-field-hint">
          参考视频支持 mp4 / webm / mov，≤80MB。请仅使用你有权使用的素材做参考；复刻他人视频的版权与合规风险由使用者自行承担。
        </p>
      </section>

      <section className="ai-video-form-card" aria-label="镜头结构">
        <div className="ai-video-form-head">
          <h3>2. 镜头结构</h3>
          <p>
            {analysis
              ? `已按画面切点解析出真实节奏骨架（全长约 ${analysis.duration}s）；生成脚本时逐镜对齐镜头数与时长，只复刻节奏不照搬内容。`
              : analyzed
                ? "暂不支持解析链接里的视频画面，以下为高转化带货视频的通用结构；AI 将据此并结合你的商品生成脚本。"
                : "先在上一栏载入结构参考。"}
          </p>
        </div>
        {storyboards.length > 0 && (
          <div className="ai-video-storyboard-grid">
            {storyboards.map((card) => (
              <article key={card.id} className="ai-video-storyboard-card">
                <span>{card.id}</span>
                <strong>{card.title}</strong>
                <p>{card.description}</p>
                <em>{card.duration}</em>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="ai-video-form-card" aria-label="商品信息">
        <div className="ai-video-form-head">
          <h3>3. 你的商品</h3>
          <p>商品图与卖点会一起带进工作台，作为脚本与分镜的输入。</p>
        </div>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(event) => {
            addImages(event.target.files);
            event.target.value = "";
          }}
        />
        <div className="ai-video-upload">
          <button
            type="button"
            className="ai-video-upload-drop"
            disabled={images.length >= MAX_CLONE_IMAGES}
            onClick={() => imageInputRef.current?.click()}
          >
            <strong>商品图（{images.length}/{MAX_CLONE_IMAGES}）</strong>
            <span>支持 JPG / PNG，最多 5 张</span>
          </button>
          {images.length > 0 && (
            <div className="ai-video-thumbs">
              {images.map((image) => (
                <figure key={image.id} className="ai-video-thumb">
                  <img src={image.previewUrl} alt="" />
                  <button type="button" aria-label="移除图片" onClick={() => removeImage(image.id)}>×</button>
                </figure>
              ))}
            </div>
          )}
        </div>
        <div className="ai-video-settings-grid">
          <div className="ai-video-field">
            <label htmlFor="ai-video-clone-name">商品名称 *</label>
            <input
              id="ai-video-clone-name"
              value={productName}
              placeholder="输入你的商品名称"
              onChange={(event) => setProductName(event.target.value)}
            />
          </div>
          <div className="ai-video-field">
            <label htmlFor="ai-video-clone-features">商品卖点 *</label>
            <input
              id="ai-video-clone-features"
              value={sellingPoints}
              placeholder="核心卖点、优势特性"
              onChange={(event) => setSellingPoints(event.target.value)}
            />
          </div>
        </div>
      </section>

      {handoffError && <div className="ai-video-form-error" role="alert">{handoffError}</div>}

      <div className="ai-video-clone-handoff">
        <p>
          下一步把节奏结构与商品信息预填进视频工作台：脚本风格、目标时长与出片策略在那里确认后，再统一创建项目与生成脚本。
        </p>
        <button type="button" className="ai-video-submit" disabled={!canHandoff} onClick={() => void handleHandoff()}>
          {handingOff ? "正在准备创作简报…" : "去工作台生成脚本"}
        </button>
      </div>
    </section>
  );
}
