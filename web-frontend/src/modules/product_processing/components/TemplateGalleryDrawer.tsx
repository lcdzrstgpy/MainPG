import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import '../styles/TemplateGalleryDrawer.css';

/** 轮播自动切换节奏：多图时每张停留 4 秒。 */
const AUTO_PLAY_INTERVAL = 4000;

type TemplateSlide = { src: string; caption: string };
type ImageTemplateId = 'A' | 'B' | 'C' | 'D';
type DescTemplateId = 'A' | 'B';

// 生图提示词模板（对齐后端 IMAGE_TEMPLATES：A/B/C/D 直观标题区分四套生图逻辑）
const IMAGE_TEMPLATES: { id: ImageTemplateId; name: string; description: string }[] = [
  { id: 'A', name: '影棚无缝主图', description: '干净影棚无缝背景：主体居中对称、三点柔光棚拍、极简几何道具，标准电商主图质感，画面无文字' },
  { id: 'B', name: '人景叙事（防比价）', description: '人+空间编辑大片：局部模特入镜、中景三分构图、真实居家叙事场景，材质显贵、难以搜图比价，画面无文字' },
  { id: 'C', name: '静物平铺俯拍', description: '90° 正俯视平铺 knolling：桌面即背景、行列对齐编排、顶部均匀柔光，清爽目录级质感，画面无文字' },
  { id: 'D', name: '环境远景', description: '广角环境建立镜头：主体小、空间主导、前中后三层纵深、真实家具软装+自然窗光，电影感生活氛围，画面无文字' },
];

// 五点描述模板（对齐后端 DESC_TEMPLATES：A/B 直观标题区分两套文案写法）
const DESC_TEMPLATES: { id: DescTemplateId; name: string; description: string }[] = [
  { id: 'A', name: '卖点前置（结果导向）', description: '每条先给买家结果/收益，再用可核验的产品事实佐证，说服力强' },
  { id: 'B', name: '场景代入（痛点导向）', description: '每条先锚定具体使用场景或人群，再用可核验事实解决，代入感强' },
];

// 每套模板的效果轮播图：图片放在 public/assets/template-gallery/ 下，按下方文件名补齐即可
// 自动轮播（缺图会被自动剔除，不足两张则不轮播）。命名规则：<类型>-<模板字母>-<序号>.png
const IMAGE_TEMPLATE_SLIDES: Record<ImageTemplateId, TemplateSlide[]> = {
  A: [{ src: '/assets/template-gallery/image-a-1.png', caption: '影棚无缝效果' }],
  B: [{ src: '/assets/template-gallery/image-b-1.png', caption: '人景叙事效果' }],
  C: [{ src: '/assets/template-gallery/image-c-1.png', caption: '静物平铺效果' }],
  D: [{ src: '/assets/template-gallery/image-d-1.png', caption: '环境远景效果' }],
};

const DESC_TEMPLATE_SLIDES: Record<DescTemplateId, TemplateSlide[]> = {
  A: [],
  B: [],
};

/**
 * 模板效果轮播：方形画框 + 淡入切换 + 角标与指示点，悬停暂停。
 * 卡片本身是按钮，指示点点击时 stopPropagation，避免误触发选中。加载失败的图会被剔除。
 */
function TemplateCarousel({ slides }: { slides: TemplateSlide[] }) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [broken, setBroken] = useState<string[]>([]);

  const available = useMemo(() => slides.filter((s) => !broken.includes(s.src)), [slides, broken]);
  const current = available.length ? Math.min(index, available.length - 1) : 0;

  useEffect(() => {
    if (paused || available.length < 2) return;
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % available.length), AUTO_PLAY_INTERVAL);
    return () => window.clearInterval(timer);
  }, [paused, available.length]);

  if (!available.length) return null;

  return (
    <span
      className="template-card-carousel"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {available.map((slide, i) => (
        <img
          key={slide.src}
          className={`template-card-carousel-img${i === current ? ' is-active' : ''}`}
          src={slide.src}
          alt=""
          loading="lazy"
          draggable={false}
          onError={() => setBroken((prev) => (prev.includes(slide.src) ? prev : [...prev, slide.src]))}
        />
      ))}
      <span className="template-card-carousel-footer">
        <span className="template-card-carousel-caption">{available[current].caption}</span>
        {available.length > 1 && (
          <span className="template-card-carousel-dots">
            {available.map((slide, i) => (
              <button
                key={slide.src}
                type="button"
                className={`template-card-carousel-dot${i === current ? ' is-active' : ''}`}
                aria-label={`查看${slide.caption}`}
                onClick={(e) => { e.stopPropagation(); setIndex(i); }}
              />
            ))}
          </span>
        )}
      </span>
    </span>
  );
}

type Props = {
  imageTemplate?: ImageTemplateId;
  descriptionTemplate?: DescTemplateId;
  /** 一键随机模板：开启时忽略选定模板，本批次每条链接随机分配，故触发区显示「随机分配」。 */
  random?: boolean;
  onSelectImage: (id: ImageTemplateId) => void;
  onSelectDescription: (id: DescTemplateId) => void;
  /** 行内紧凑模式：与站点/语言下拉同排，只保留单行当前值与箭头。 */
  compact?: boolean;
};

/**
 * 生图 / 描述模板效果选择抽屉。
 *
 * 从「处理设置」中剥离出来：触发区只显示当前选择，点开后以抽屉展示每套模板的
 * 效果轮播图（图片素材放 public/assets/template-gallery/），点选即生效。
 */
export function TemplateGalleryDrawer({
  imageTemplate = 'A',
  descriptionTemplate = 'A',
  random = false,
  onSelectImage,
  onSelectDescription,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const imageName = IMAGE_TEMPLATES.find((t) => t.id === imageTemplate)?.name ?? '';
  const descName = DESC_TEMPLATES.find((t) => t.id === descriptionTemplate)?.name ?? '';

  return (
    <>
      <section className={`template-gallery-panel${compact ? ' is-inline' : ''} ${open ? 'is-open' : ''}`}>
        <button
          type="button"
          className="template-gallery-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {compact ? (
            <>
              <span className="template-gallery-toggle-value">
                {random ? '随机分配' : `${imageName} · ${descName}`}
              </span>
              <span className="template-gallery-toggle-caret" aria-hidden="true">▾</span>
            </>
          ) : (
            <>
              <span className="template-gallery-toggle-title">
                生图 / 描述模板
                <small>{random ? '当前：随机分配（每条链接随机）' : `当前：${imageName} · ${descName}`}</small>
              </span>
              <span className="template-gallery-toggle-caret">{open ? '关闭 ✕' : '选择模板 ▸'}</span>
            </>
          )}
        </button>
      </section>
      {/* portal 到 body：避免被工作区面板的层叠上下文/吸顶栏盖住头部。 */}
      {open && createPortal(
        <div className="template-gallery-layer">
          <button
            type="button"
            className="template-gallery-backdrop"
            onClick={() => setOpen(false)}
            aria-label="关闭模板选择"
          />
          <aside className="template-gallery-drawer" role="dialog" aria-modal="true" aria-label="选择生图 / 描述模板">
            <header className="template-gallery-header">
              <div>
                <span>TEMPLATES</span>
                <h2>生图 / 描述模板</h2>
                <p>{random ? '已开启「一键随机模板」：本批次每条链接随机分配，下面的点选暂不生效' : '点选即生效，对后续新任务应用；每套模板的效果以轮播图展示'}</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="template-gallery-body">
              <section className="template-gallery-group">
                <h3>生图模板</h3>
                <div className="template-card-grid">
                  {IMAGE_TEMPLATES.map((template) => {
                    const active = template.id === imageTemplate;
                    const slides = IMAGE_TEMPLATE_SLIDES[template.id] ?? [];
                    return (
                      <button
                        key={template.id}
                        type="button"
                        className={`template-card${active ? ' is-active' : ''}`}
                        onClick={() => onSelectImage(template.id)}
                        aria-pressed={active}
                      >
                        {slides.length > 0 && (
                          <span className="template-card-media">
                            <TemplateCarousel slides={slides} />
                          </span>
                        )}
                        <span className="template-card-body">
                          <strong>{template.name}</strong>
                          <small>{template.description}</small>
                        </span>
                        {active && <span className="template-card-check">✓</span>}
                      </button>
                    );
                  })}
                </div>
              </section>
              <section className="template-gallery-group">
                <h3>描述模板</h3>
                <div className="template-card-grid">
                  {DESC_TEMPLATES.map((template) => {
                    const active = template.id === descriptionTemplate;
                    const slides = DESC_TEMPLATE_SLIDES[template.id] ?? [];
                    return (
                      <button
                        key={template.id}
                        type="button"
                        className={`template-card${active ? ' is-active' : ''}`}
                        onClick={() => onSelectDescription(template.id)}
                        aria-pressed={active}
                      >
                        {slides.length > 0 && (
                          <span className="template-card-media">
                            <TemplateCarousel slides={slides} />
                          </span>
                        )}
                        <span className="template-card-body">
                          <strong>{template.name}</strong>
                          <small>{template.description}</small>
                        </span>
                        {active && <span className="template-card-check">✓</span>}
                      </button>
                    );
                  })}
                </div>
              </section>
            </div>
          </aside>
        </div>, document.body)}
    </>
  );
}

export default TemplateGalleryDrawer;
