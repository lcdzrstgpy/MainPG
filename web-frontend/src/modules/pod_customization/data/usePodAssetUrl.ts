import { createElement, useEffect, useRef, useState, type ImgHTMLAttributes } from "react";

import { getAuthToken, httpBlob } from "../../../transport/http/client";

async function loadPodAsset(path: string): Promise<Blob> {
  if (!/^https?:\/\//i.test(path)) return httpBlob(path);
  const token = getAuthToken();
  const response = await fetch(path, {
    headers: token ? { authorization: `Bearer ${token}` } : undefined,
  });
  if (!response.ok) throw new Error(`素材加载失败（状态码 ${response.status}），请稍后重试`);
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
};

export function PodAssetImage({ path, loading, ...props }: PodAssetImageProps) {
  const reference = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(loading !== "lazy");
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
  const url = usePodAssetUrl(path, visible);
  if (!visible) return createElement("span", { ref: reference, "aria-hidden": true });
  return url ? createElement("img", { ...props, src: url }) : null;
}
