import { useEffect, useMemo, useRef, useState } from "react";
import { Group, Image as KonvaImage, Layer, Rect, Stage, Text } from "react-konva";
import type Konva from "konva";

import {
  COMPOSITION_BACKGROUNDS,
  COMPOSITION_TEMPLATES,
  compositionTemplateById,
  type CompositionTemplateId,
} from "../data/compositionTemplates";
import {
  matchPropCards,
  propCardById,
  type PropCard,
} from "../data/propCardLibrary";
import "./LocalCompositionDrawer.css";

const PREVIEW_SIZE = 640;
const DESIGN_SIZE = 2000;

type SpecRow = { label: string; value: string };

type Props = {
  open: boolean;
  title: string;
  subtitle?: string;
  category: string;
  productImageUrl: string;
  specRows?: SpecRow[];
  onClose: () => void;
  onSave: (file: File) => Promise<void> | void;
};

function useLoadedImage(url: string): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (!url) {
      setImage(null);
      return;
    }
    const next = new window.Image();
    next.crossOrigin = "anonymous";
    next.onload = () => setImage(next);
    next.onerror = () => setImage(null);
    next.src = url;
    return () => {
      next.onload = null;
      next.onerror = null;
    };
  }, [url]);
  return image;
}

function fitImage(
  image: HTMLImageElement,
  box: { width: number; height: number },
  mode: "contain" | "cover",
) {
  const scale =
    mode === "cover"
      ? Math.max(box.width / image.width, box.height / image.height)
      : Math.min(box.width / image.width, box.height / image.height);
  const width = image.width * scale;
  const height = image.height * scale;
  return {
    x: (box.width - width) / 2,
    y: (box.height - height) / 2,
    width,
    height,
  };
}

function verticalText(value: string): string {
  return value.split("").join("\n");
}

export function LocalCompositionDrawer({
  open,
  title,
  subtitle: subtitleProp,
  category,
  productImageUrl,
  specRows,
  onClose,
  onSave,
}: Props) {
  const stageRef = useRef<Konva.Stage | null>(null);
  const productImage = useLoadedImage(productImageUrl);
  const [templateId, setTemplateId] = useState<CompositionTemplateId>("headline");
  const [headline, setHeadline] = useState(title || "产品卖点");
  const [subtitle, setSubtitle] = useState(subtitleProp || category || "DETAIL");
  const [background, setBackground] = useState(COMPOSITION_BACKGROUNDS[0]);
  const [selectedPropIds, setSelectedPropIds] = useState<string[]>([]);
  const [productScale, setProductScale] = useState(1);
  const [propOffsets, setPropOffsets] = useState<Record<string, { x: number; y: number }>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const template = compositionTemplateById(templateId);
  const recommended = useMemo(
    () => matchPropCards(category, templateId, 8),
    [category, templateId],
  );

  useEffect(() => {
    if (!open) return;
    setHeadline(title || "产品卖点");
    setSubtitle(subtitleProp || category || "DETAIL");
    setTemplateId("headline");
    setBackground(COMPOSITION_BACKGROUNDS[0]);
    setProductScale(1);
    setPropOffsets({});
    setMessage("");
  }, [open, title, subtitleProp, category]);

  useEffect(() => {
    setPropOffsets({});
    setProductScale(1);
  }, [templateId]);

  useEffect(() => {
    const maxSlots = compositionTemplateById(templateId).props.length;
    setSelectedPropIds((current) =>
      current
        .slice(0, maxSlots)
        .filter((id) => propCardById(id) != null),
    );
  }, [templateId]);

  const selectedProps = useMemo(
    () =>
      selectedPropIds
        .map((id) => propCardById(id))
        .filter((card): card is PropCard => card != null),
    [selectedPropIds],
  );

  const toggleProp = (id: string) => {
    setPropOffsets({});
    setSelectedPropIds((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id);
      if (current.length >= template.props.length) return current;
      return [...current, id];
    });
  };

  const rows = (specRows && specRows.length > 0
    ? specRows
    : [
        { label: "尺寸", value: "20 × 8 × 4.5 cm" },
        { label: "材质", value: "PP / 环保材质" },
        { label: "重量", value: "138 g" },
      ]
  ).slice(0, 3);

  const productFitted = productImage
    ? fitImage(productImage, template.product, template.product.fit)
    : null;

  const exportPng = async () => {
    if (!stageRef.current || !open) return;
    setBusy(true);
    setMessage("");
    try {
      const dataUrl = stageRef.current.toDataURL({
        pixelRatio: DESIGN_SIZE / PREVIEW_SIZE,
        mimeType: "image/png",
      });
      const response = await fetch(dataUrl);
      const blob = await response.blob();
      const file = new File([blob], `composition-${Date.now()}.png`, {
        type: "image/png",
      });
      await onSave(file);
      setMessage("已加入详情图，可继续调整或关闭");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导出失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="composition-overlay" role="dialog" aria-modal="true" aria-label="本地拼装">
      <div className="composition-drawer">
        <header className="composition-head">
          <div>
            <h2>本地拼装</h2>
            <p>用产品主图 + 自建配件 + 文案，本地合成详情图，不调用 AI。</p>
          </div>
          <button type="button" className="composition-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="composition-body">
          <section className="composition-preview-pane">
            <div className="composition-template-row">
              {COMPOSITION_TEMPLATES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`composition-template-card${templateId === item.id ? " is-active" : ""}`}
                  onClick={() => setTemplateId(item.id)}
                  title={item.description}
                >
                  <span className={`composition-template-thumb template-${item.id}`} />
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </button>
              ))}
            </div>

            <div className="composition-canvas-frame">
              <Stage
                ref={stageRef}
                width={PREVIEW_SIZE}
                height={PREVIEW_SIZE}
                className="composition-canvas"
              >
                <Layer scaleX={PREVIEW_SIZE / DESIGN_SIZE} scaleY={PREVIEW_SIZE / DESIGN_SIZE}>
                  <Rect width={DESIGN_SIZE} height={DESIGN_SIZE} fill={background} />

                  {template.verticalLabel && (
                    <Text
                      x={template.verticalLabel.x}
                      y={template.verticalLabel.y}
                      width={70}
                      text={verticalText(template.verticalLabel.text)}
                      fontSize={template.verticalLabel.fontSize}
                      lineHeight={1.25}
                      align="center"
                      fill={template.verticalLabel.color}
                      fontFamily="Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
                      fontStyle="bold"
                    />
                  )}

                  <Rect
                    x={template.product.x}
                    y={template.product.y}
                    width={template.product.width}
                    height={template.product.height}
                    fill="rgba(255,255,255,0.34)"
                    cornerRadius={28}
                  />
                  {productImage && productFitted && (
                    <Group
                      x={template.product.x}
                      y={template.product.y}
                      clipX={0}
                      clipY={0}
                      clipWidth={template.product.width}
                      clipHeight={template.product.height}
                    >
                      <KonvaImage
                        image={productImage}
                        x={productFitted.x + productFitted.width / 2}
                        y={productFitted.y + productFitted.height / 2}
                        offsetX={productFitted.width / 2}
                        offsetY={productFitted.height / 2}
                        width={productFitted.width}
                        height={productFitted.height}
                        scaleX={productScale}
                        scaleY={productScale}
                      />
                    </Group>
                  )}

                  <Text
                    x={template.headline.x}
                    y={template.headline.y}
                    width={template.headline.width}
                    text={headline || "产品卖点"}
                    fontSize={template.headline.fontSize}
                    lineHeight={1.08}
                    fill={template.headline.color}
                    fontFamily="Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
                    fontStyle="bold"
                    wrap="char"
                    height={template.headline.fontSize * 2.3}
                    ellipsis
                  />

                  {template.subtitle && (
                    <>
                      <Rect
                        x={template.subtitle.x}
                        y={template.subtitle.y}
                        width={template.subtitle.width}
                        height={template.subtitle.fontSize * 1.8}
                        fill={template.subtitle.background}
                        cornerRadius={template.subtitle.fontSize * 0.9}
                      />
                      <Text
                        x={template.subtitle.x}
                        y={template.subtitle.y}
                        width={template.subtitle.width}
                        height={template.subtitle.fontSize * 1.8}
                        text={subtitle || "DETAIL"}
                        fontSize={template.subtitle.fontSize}
                        fill={template.subtitle.color}
                        align="center"
                        verticalAlign="middle"
                        fontFamily="Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
                        fontStyle="bold"
                        wrap="char"
                        ellipsis
                      />
                    </>
                  )}

                  {template.spec && (
                    <SpecCard
                      x={template.spec.x}
                      y={template.spec.y}
                      width={template.spec.width}
                      rowHeight={template.spec.rowHeight}
                      fontSize={template.spec.fontSize}
                      rows={rows}
                    />
                  )}

                  {selectedProps.slice(0, template.props.length).map((card, index) => {
                    const slot = template.props[index];
                    const key = `${templateId}:${index}`;
                    const offset = propOffsets[key] ?? { x: 0, y: 0 };
                    return (
                      <DraggableProp
                        key={card.id}
                        card={card}
                        x={slot.x + offset.x}
                        y={slot.y + offset.y}
                        size={slot.size}
                        onMove={(x, y) =>
                          setPropOffsets((current) => ({
                            ...current,
                            [key]: { x: x - slot.x, y: y - slot.y },
                          }))
                        }
                      />
                    );
                  })}
                </Layer>
              </Stage>
            </div>

            <div className="composition-backgrounds">
              <span>背景</span>
              {COMPOSITION_BACKGROUNDS.map((color) => (
                <button
                  key={color}
                  type="button"
                  className={`composition-color${background === color ? " is-active" : ""}`}
                  style={{ background: color }}
                  onClick={() => setBackground(color)}
                  aria-label={`背景 ${color}`}
                />
              ))}
            </div>
          </section>

          <aside className="composition-controls">
            <label className="composition-field">
              <span>主标题</span>
              <textarea
                rows={2}
                value={headline}
                onChange={(event) => setHeadline(event.target.value)}
                placeholder="例如：大容量设计。一盒搞定"
              />
            </label>
            <label className="composition-field">
              <span>副标题胶囊</span>
              <input
                value={subtitle}
                onChange={(event) => setSubtitle(event.target.value)}
                placeholder="例如：贴心设计 用笔不再凌乱"
              />
            </label>

            <label className="composition-field">
              <span>产品图大小</span>
              <input
                type="range"
                min="0.7"
                max="1.3"
                step="0.05"
                value={productScale}
                onChange={(event) => setProductScale(Number(event.target.value))}
              />
            </label>

            <div className="composition-section-head">
              <strong>推荐配件</strong>
              <span>已选 {selectedProps.length} / {template.props.length}</span>
            </div>
            <p className="composition-tip">道具加入后可在画布上直接拖动位置。</p>
            <div className="composition-prop-grid">
              {recommended.map((card) => {
                const active = selectedPropIds.includes(card.id);
                return (
                  <button
                    key={card.id}
                    type="button"
                    className={`composition-prop-card${active ? " is-active" : ""}`}
                    onClick={() => toggleProp(card.id)}
                    title={active ? "点击取消" : "点击加入拼装"}
                  >
                    <img src={card.imageUrl} alt={card.label} />
                    <span>{card.label}</span>
                  </button>
                );
              })}
            </div>
            <p className="composition-tip">展示道具仅作本地拼装画面搭配，不随商品出售；AI 生图的配件请在「四格逐格卡片」里设置。</p>

            {message && <p className="composition-message">{message}</p>}

            <button
              type="button"
              className="composition-save"
              onClick={() => void exportPng()}
              disabled={busy || !productImage}
            >
              {busy ? "导出中…" : "导出并加入详情"}
            </button>
          </aside>
        </div>
      </div>
    </div>
  );
}

function DraggableProp({
  card,
  x,
  y,
  size,
  onMove,
}: {
  card: PropCard;
  x: number;
  y: number;
  size: number;
  onMove: (x: number, y: number) => void;
}) {
  const image = useLoadedImage(card.imageUrl);
  if (!image) return null;
  return (
    <KonvaImage
      image={image}
      x={x}
      y={y}
      width={size}
      height={size}
      opacity={0.94}
      draggable
      onDragEnd={(event) => onMove(event.target.x(), event.target.y())}
    />
  );
}

function SpecCard({
  x,
  y,
  width,
  rowHeight,
  fontSize,
  rows,
}: {
  x: number;
  y: number;
  width: number;
  rowHeight: number;
  fontSize: number;
  rows: SpecRow[];
}) {
  const height = rowHeight * rows.length + 40;
  return (
    <>
      <Rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill="rgba(255,255,255,0.82)"
        cornerRadius={24}
      />
      {rows.map((row, index) => (
        <Text
          key={`${row.label}-${index}`}
          x={x + 42}
          y={y + 20 + index * rowHeight}
          width={width - 84}
          text={`${row.label}    ${row.value}`}
          fontSize={fontSize}
          fill="#4a3b2c"
          fontFamily="Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
          fontStyle="bold"
          wrap="char"
          ellipsis
        />
      ))}
    </>
  );
}

export default LocalCompositionDrawer;
