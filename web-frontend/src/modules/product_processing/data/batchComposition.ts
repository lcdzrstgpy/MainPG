import {
  COMPOSITION_BACKGROUNDS,
  compositionTemplateById,
  type CompositionTemplateId,
} from './compositionTemplates';

export type BatchSpecRow = { label: string; value: string };

export type BatchCompositionInput = {
  templateId: CompositionTemplateId;
  productImageUrl: string;
  headline: string;
  subtitle: string;
  specRows?: BatchSpecRow[];
  background?: string;
};

const SIZE = 2000;
const FONT_FAMILY = '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif';

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new window.Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('产品图加载失败，无法自动拼装'));
    image.src = url;
  });
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.arcTo(x + width, y, x + width, y + height, r);
  context.arcTo(x + width, y + height, x, y + height, r);
  context.arcTo(x, y + height, x, y, r);
  context.arcTo(x, y, x + width, y, r);
  context.closePath();
}

function wrapLines(
  context: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number,
): string[] {
  const chars = Array.from(text || '');
  const lines: string[] = [];
  let current = '';
  for (const char of chars) {
    const candidate = current + char;
    if (context.measureText(candidate).width <= maxWidth) {
      current = candidate;
      continue;
    }
    if (current) lines.push(current);
    current = char;
    if (lines.length >= maxLines) break;
  }
  if (current && lines.length < maxLines) lines.push(current);
  if (!lines.length) return [''];
  const consumed = lines.join('').length;
  if (consumed < chars.length) {
    let last = lines[lines.length - 1] ?? '';
    while (last.length > 1 && context.measureText(`${last}…`).width > maxWidth) {
      last = last.slice(0, -1);
    }
    lines[lines.length - 1] = `${last}…`;
  }
  return lines;
}

export async function renderBatchComposition(
  input: BatchCompositionInput,
): Promise<Blob> {
  const template = compositionTemplateById(input.templateId);
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('当前环境不支持 Canvas');

  context.fillStyle = input.background || COMPOSITION_BACKGROUNDS[0];
  context.fillRect(0, 0, SIZE, SIZE);

  const image = await loadImage(input.productImageUrl);
  const containScale = Math.min(
    template.product.width / image.width,
    template.product.height / image.height,
  );
  const drawWidth = image.width * containScale;
  const drawHeight = image.height * containScale;
  const drawX = template.product.x + (template.product.width - drawWidth) / 2;
  const drawY = template.product.y + (template.product.height - drawHeight) / 2;

  context.save();
  roundedRect(
    context,
    template.product.x,
    template.product.y,
    template.product.width,
    template.product.height,
    28,
  );
  context.clip();
  context.drawImage(image, drawX, drawY, drawWidth, drawHeight);
  context.restore();

  context.fillStyle = template.headline.color;
  context.font = `bold ${template.headline.fontSize}px ${FONT_FAMILY}`;
  context.textBaseline = 'top';
  const lines = wrapLines(
    context,
    input.headline || 'Product Details',
    template.headline.width,
    2,
  );
  lines.forEach((line, index) => {
    context.fillText(
      line,
      template.headline.x,
      template.headline.y + index * template.headline.fontSize * 1.12,
    );
  });

  if (template.subtitle) {
    context.fillStyle = template.subtitle.background;
    roundedRect(
      context,
      template.subtitle.x,
      template.subtitle.y,
      template.subtitle.width,
      template.subtitle.fontSize * 1.8,
      template.subtitle.fontSize * 0.9,
    );
    context.fill();
    context.fillStyle = template.subtitle.color;
    context.font = `bold ${template.subtitle.fontSize}px ${FONT_FAMILY}`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(
      input.subtitle || 'DETAIL',
      template.subtitle.x + template.subtitle.width / 2,
      template.subtitle.y + template.subtitle.fontSize * 0.9,
      template.subtitle.width - 40,
    );
    context.textAlign = 'start';
  }

  if (template.verticalLabel) {
    context.fillStyle = template.verticalLabel.color;
    context.font = `bold ${template.verticalLabel.fontSize}px ${FONT_FAMILY}`;
    context.textAlign = 'center';
    context.textBaseline = 'top';
    Array.from(template.verticalLabel.text).forEach((char, index) => {
      context.fillText(
        char,
        template.verticalLabel!.x + 35,
        template.verticalLabel!.y +
          index * template.verticalLabel!.fontSize * 1.25,
      );
    });
    context.textAlign = 'start';
  }

  if (template.spec) {
    const rows = (input.specRows && input.specRows.length
      ? input.specRows
      : [
          { label: '尺寸', value: '—' },
          { label: '重量', value: '—' },
          { label: 'SKU', value: '—' },
        ]
    ).slice(0, 3);
    const height = template.spec.rowHeight * rows.length + 40;
    context.fillStyle = 'rgba(255,255,255,0.82)';
    roundedRect(
      context,
      template.spec.x,
      template.spec.y,
      template.spec.width,
      height,
      24,
    );
    context.fill();
    context.fillStyle = '#4a3b2c';
    context.font = `bold ${template.spec.fontSize}px ${FONT_FAMILY}`;
    context.textBaseline = 'top';
    rows.forEach((row, index) => {
      context.fillText(
        `${row.label}    ${row.value}`,
        template.spec!.x + 42,
        template.spec!.y + 20 + index * template.spec!.rowHeight,
        template.spec!.width - 84,
      );
    });
  }

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('拼装图导出失败'));
    }, 'image/png');
  });
}
