import { useEffect, useRef } from "react";
import type { TextareaHTMLAttributes } from "react";

/**
 * 随内容自动增高的多行输入：始终完整展示文字，不出现内部滚动条。
 */
export function AutoGrowTextarea({ value, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const resize = () => {
      const style = window.getComputedStyle(element);
      const border =
        (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight + border}px`;
    };
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [value]);

  return <textarea ref={ref} rows={1} value={value} {...rest} />;
}
