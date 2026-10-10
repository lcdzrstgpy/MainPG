import { createElement, useEffect, useRef, useState, type ImgHTMLAttributes, type SyntheticEvent } from "react";

import { httpBlob } from "../../../transport/http/client";

async function loadPodAsset(path: string): Promise<Blob> {
  if (!/^https?:\/\//i.test(path)) return httpBlob(path);
  const response = await fetch(path);
  if (!response.ok) throw new Error(`素材加载失败 (HTTP ${response.status})`);
  return response.blob();
}

export function usePodAssetUrl(path?: string, enabled = true): string {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let stopped = false;
    let objectUrl = "";
    setUrl("");
    if (!path || !enabled) return;
    if (/^(blob:|data:)/i.test(path)) {
      setUrl(path);
      return;
    }
    // Public external URL (e.g. COS 图床外链): display it online directly via
    // <img src>, which needs no CORS. Do NOT fetch()->blob() here: that path
    // requires the cross-origin host to send Access-Control-Allow-Origin, and
    // the COS bucket does not, so the image silently renders blank.
    if (/^https?:\/\//i.test(path)) {
      setUrl(path);
      return;
    }
    void loadPodAsset(path).then((blob) => {
      if (stopped) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => {
      if (!stopped) setUrl("");
    });
    return () => {
      stopped = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [enabled, path]);

  return url;
}

type PodAssetImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  path?: string;
  /** 主地址（通常是图床公网链接）加载失败时回退的本地地址。 */
  fallbackPath?: string;
};

export function PodAssetImage({ path, fallbackPath, loading, onError, ...props }: PodAssetImageProps) {
  const reference = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(loading !== "lazy");
  const [stage, setStage] = useState<"primary" | "fallback">("primary");
  useEffect(() => {
    if (loading !== "lazy") {
      setVisible(true);
      return;
    }
    const target = reference.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "320px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, [loading]);
  // 切换图片（列表复用时）必须回到主地址，否则会沿用上一张的回退状态。
  useEffect(() => {
    setStage("primary");
  }, [path, fallbackPath]);
  const primaryUrl = usePodAssetUrl(path, visible && stage === "primary");
  const fallbackUrl = usePodAssetUrl(fallbackPath, visible && stage === "fallback");
  if (!visible) return createElement("span", { ref: reference, "aria-hidden": true });
  const url = stage === "fallback" ? fallbackUrl : primaryUrl;
  if (!url) return null;
  return createElement("img", {
    ...props,
    src: url,
    onError: (event: SyntheticEvent<HTMLImageElement>) => {
      onError?.(event);
      // 图床链接不可用（被删、失效、跨域被拦）时，回退到本地资产地址。
      if (stage === "primary" && fallbackPath) setStage("fallback");
    },
  });
}
