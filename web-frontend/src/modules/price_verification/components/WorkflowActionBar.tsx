import { forwardRef, useEffect, useRef, type ReactNode } from "react";
import "../styles/workflowActionBar.css";

type Props = {
  children: ReactNode;
  label: string;
  floating?: boolean;
};

/** Keeps the current step's primary actions visible while long lists scroll. */
export const WorkflowActionBar = forwardRef<HTMLElement, Props>(function WorkflowActionBar({ children, label, floating = false }, ref) {
  return <aside ref={ref} className={`price-verification-action-bar${floating ? " is-floating" : ""}`} aria-label={label}>
    {children}
  </aside>;
});

/** Pins an action bar to a workspace edge once its natural position scrolls away. */
export function useFloatingActionBar(edge: "bottom" | "top" = "bottom") {
  const actionBarRef = useRef<HTMLElement>(null);
  const spacerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bar = actionBarRef.current;
    const spacer = spacerRef.current;
    const contentCard = document.querySelector<HTMLElement>(".content-card");
    if (!bar || !spacer) return;
    let stuck = false;
    let naturalTop = 0;

    const reset = () => {
      bar.style.position = "";
      bar.style.top = "";
      bar.style.bottom = "";
      bar.style.left = "";
      bar.style.width = "";
      bar.style.zIndex = "";
      spacer.style.height = "";
      bar.classList.remove("is-stuck");
    };

    const update = () => {
      const scrollTop = contentCard && contentCard.scrollHeight > contentCard.clientHeight + 1
        ? contentCard.scrollTop
        : window.scrollY || document.documentElement.scrollTop;
      const topbar = document.querySelector<HTMLElement>(".topbar-card");
      const threshold = Math.max(0, Math.round(topbar?.getBoundingClientRect().bottom ?? 0)) + 8;
      if (!stuck) naturalTop = bar.getBoundingClientRect().top + scrollTop;
      const viewportTop = naturalTop - scrollTop;

      if (!stuck && viewportTop <= threshold) {
        const rect = bar.getBoundingClientRect();
        stuck = true;
        spacer.style.height = `${bar.offsetHeight}px`;
        bar.style.position = "fixed";
        if (edge === "top") {
          bar.style.top = `${threshold}px`;
          bar.style.zIndex = "21";
        } else {
          bar.style.bottom = "14px";
        }
        bar.style.left = `${Math.round(rect.left)}px`;
        bar.style.width = `${Math.round(rect.width)}px`;
        bar.classList.add("is-stuck");
      } else if (stuck) {
        const spacerRect = spacer.getBoundingClientRect();
        bar.style.left = `${Math.round(spacerRect.left)}px`;
        bar.style.width = `${Math.round(spacerRect.width)}px`;
        if (viewportTop > threshold) {
          stuck = false;
          reset();
        }
      }
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    contentCard?.addEventListener("scroll", update, { passive: true });
    const resizeObserver = new ResizeObserver(update);
    if (contentCard) resizeObserver.observe(contentCard);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      contentCard?.removeEventListener("scroll", update);
      resizeObserver.disconnect();
      reset();
    };
  }, []);

  return { actionBarRef, spacerRef };
}
