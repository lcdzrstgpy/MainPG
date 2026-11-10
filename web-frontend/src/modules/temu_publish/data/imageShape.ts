/**
 * 图片比例处理（发布页与批量发布共用）。
 *
 * Temu 对主图有两种硬要求，都是实测报错文案：
 *   · 非服饰类目轮播图「1:1、宽高 ≥800px、≤2MB」
 *   · 服饰类目轮播图「3:4，宽 >=1340，高 >=1785」
 *   · 素材图不论类目都单独校验「1:1、宽 >=800」
 * 原图直传会被拒，所以在浏览器里先居中裁一次再上传。
 *
 * 抽成独立模块的原因：单件发布（TemuPublishPage）和批量发布（batchPublish）
 * 都要用同一套尺寸。各写一份的下场是「页面能过、批量全被拒」——
 * 批量那条一开始就漏了裁切，服饰类目几乎必挂。
 */

/**
 * 服饰类目轮播图尺寸：3:4、宽 ≥1340、高 ≥1785。
 *
 * 直接用 1340×1785 差一点点不是精确 3:4（0.7507），取 **1344×1792** 两个下限都满足
 * 且比例精确等于 3:4，省掉被「应符合 3:4」二次拒的风险。
 */
export const APPAREL_CAROUSEL_WIDTH = 1344;
export const APPAREL_CAROUSEL_HEIGHT = 1792;

/** 非服饰轮播图 / 所有类目的素材图：1:1、≥800（实测下限就是 800）。 */
export const SQUARE_IMAGE_SIZE = 800;

/** 一把告诉调用方「这个类目该裁成什么尺寸」。 */
export type ImageShape = { width: number; height: number };

export const APPAREL_CAROUSEL_SHAPE: ImageShape = {
  width: APPAREL_CAROUSEL_WIDTH,
  height: APPAREL_CAROUSEL_HEIGHT,
};

export const SQUARE_SHAPE: ImageShape = { width: SQUARE_IMAGE_SIZE, height: SQUARE_IMAGE_SIZE };

/**
 * 把图片居中裁成指定比例并缩放。
 *
 * PNG 透明区域转 JPEG 会变黑，先铺白底；原图比例不符时裁掉多余部分（不拉伸）。
 */
export function toRatioDataUrl(dataUrl: string, width: number, height: number): Promise<string> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const naturalWidth = img.naturalWidth;
      const naturalHeight = img.naturalHeight;
      if (!naturalWidth || !naturalHeight) {
        resolve(dataUrl);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(dataUrl);
        return;
      }
      // 居中裁到目标比例：原图更宽就裁左右，更高就裁上下
      const targetRatio = width / height;
      const sourceRatio = naturalWidth / naturalHeight;
      let sourceWidth = naturalWidth;
      let sourceHeight = naturalHeight;
      if (sourceRatio > targetRatio) sourceWidth = Math.round(naturalHeight * targetRatio);
      else sourceHeight = Math.round(naturalWidth / targetRatio);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(
        img,
        (naturalWidth - sourceWidth) / 2,
        (naturalHeight - sourceHeight) / 2,
        sourceWidth,
        sourceHeight,
        0,
        0,
        width,
        height,
      );
      resolve(canvas.toDataURL("image/jpeg", 0.9));
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

/** 裁成 1:1 方图（非服饰主图、所有类目的素材图）。 */
export function toSquareDataUrl(dataUrl: string, size = SQUARE_IMAGE_SIZE): Promise<string> {
  return toRatioDataUrl(dataUrl, size, size);
}
