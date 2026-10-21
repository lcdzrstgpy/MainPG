import { useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";

import { reportPodAgreement } from "../api/podAgreementApi";

/**
 * POD 使用协议强制弹窗。
 *
 * 规则（产品要求）：
 * - 每次登录后首次进入任一 POD 模块时弹出一次；
 * - 必须先在弹窗内等待 8 秒，再勾选「我已阅读并同意阅读POD使用协议」，按钮才可点击；
 * - 弹窗只覆盖右侧工作区，不遮挡左侧功能栏：用户不同意也可以切到其他模块；
 *   离开 POD 模块时弹窗收起，未同意则下次进入 POD 仍会重新弹出；
 * - 没有 × / 点击遮罩 / Esc 之类的关闭方式，只能走完倒计时 + 勾选。
 *
 * 「本次登录是否已弹过」用一个 localStorage 标记表示：登录成功时置位，
 * 用户点下「已阅读」后清除。这样刷新页面、切换页签都不会重复弹，
 * 但换一次登录（重新置位）会重新弹一次；未同意就离开 POD 再回来也仍会弹。
 */
const PENDING_KEY = "wh_pod_agreement_pending";
/** 强制阅读时长：满 8 秒后才允许关闭。 */
export const POD_AGREEMENT_READING_SECONDS = 8;
/** 协议正文地址：默认指向平台公开阅读页；可用 VITE_POD_AGREEMENT_URL 覆盖（测试环境）。 */
const POD_AGREEMENT_URL =
  (import.meta.env.VITE_POD_AGREEMENT_URL as string | undefined)?.trim() ||
  "https://workbench.haocoming.top/pod-agreement";

export function markPodAgreementPending(): void {
  try {
    window.localStorage.setItem(PENDING_KEY, "1");
  } catch {
    // 无痕模式等 localStorage 不可用：忽略，最坏情况是不弹或下次登录再弹。
  }
}

export function isPodAgreementPending(): boolean {
  try {
    return window.localStorage.getItem(PENDING_KEY) === "1";
  } catch {
    return false;
  }
}

export function clearPodAgreementPending(): void {
  try {
    window.localStorage.removeItem(PENDING_KEY);
  } catch {
    // 忽略
  }
}

type PodUsageAgreementModalProps = {
  open: boolean;
  /** 勾选 + 倒计时结束后点击「已阅读」。 */
  onAgree: () => void;
};

/** 右侧工作区（不含左侧功能栏）的左边界，用作遮罩起点。 */
function measureWorkspaceLeft(): number {
  const main = document.querySelector<HTMLElement>(".workspace-main");
  return main ? Math.max(0, Math.round(main.getBoundingClientRect().left)) : 0;
}

export function PodUsageAgreementModal({ open, onAgree }: PodUsageAgreementModalProps) {
  const [remaining, setRemaining] = useState(POD_AGREEMENT_READING_SECONDS);
  const [agreed, setAgreed] = useState(false);
  // 遮罩只盖住右侧工作区：左侧功能栏保持可点击，用户可以直接切模块。
  const [leftOffset, setLeftOffset] = useState<number>(() => measureWorkspaceLeft());

  useLayoutEffect(() => {
    if (!open) return;
    const update = () => setLeftOffset(measureWorkspaceLeft());
    update();
    const main = document.querySelector<HTMLElement>(".workspace-main");
    const observer = new ResizeObserver(update);
    if (main) observer.observe(main);
    window.addEventListener("resize", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [open]);

  // 每次打开都重新计时并清空勾选：上一次的阅读进度不能带到下一次登录。
  useEffect(() => {
    if (!open) return;
    setRemaining(POD_AGREEMENT_READING_SECONDS);
    setAgreed(false);
    const timer = window.setInterval(() => {
      setRemaining((value) => {
        if (value <= 1) {
          window.clearInterval(timer);
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  // 霸屏期间锁住背景滚动。
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;

  const countdownDone = remaining <= 0;
  const canAgree = countdownDone && agreed;

  return createPortal(
    <div
      className="pod-agreement-mask"
      role="presentation"
      style={{ left: leftOffset }}
    >
      <section
        className="pod-agreement-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pod-agreement-title"
        aria-describedby="pod-agreement-desc"
      >
        <header className="pod-agreement-head">
          <span className="pod-agreement-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3 4.5 5.8v5.4c0 4.4 3.1 8.1 7.5 9.3 4.4-1.2 7.5-4.9 7.5-9.3V5.8L12 3Z" />
              <path d="m8.8 11.8 2.2 2.2 4.4-4.4" />
            </svg>
          </span>
          <div className="pod-agreement-heading">
            <span className="pod-agreement-eyebrow">POD USAGE NOTICE</span>
            <h2 id="pod-agreement-title">POD 使用须知</h2>
          </div>
        </header>
        <div className="pod-agreement-body" id="pod-agreement-desc">
          <p>亲爱的用户朋友：</p>
          <p>
            POD有高度自由化产出功能，具有较强的发散性与不确定性，产品细节校订可以在本批次高级创意编辑中强调，以减少产品畸变概率；同时本系统以用户使用体验为宗旨，将会不断学习并优化用户体验。如有生成失败，系统会自动退回对应款式积分，成功款式将会进行扣除。同时每款左上角绿色选项可反选，以此取消导出，排除不想要的款式。
          </p>
        </div>
        <footer className="pod-agreement-foot">
          <label className="pod-agreement-check">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
            />
            <span>
              我已阅读并同意
              <a
                className="pod-agreement-link"
                href={POD_AGREEMENT_URL}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => event.stopPropagation()}
              >
                《POD使用协议》
              </a>
            </span>
          </label>
          <button
            type="button"
            className="pod-agreement-action"
            disabled={!canAgree}
            onClick={() => {
              // 上报服务器（本地后端只做转发），不阻塞关闭。
              void reportPodAgreement(true);
              onAgree();
            }}
            title={!agreed ? "请先勾选POD使用协议" : !countdownDone ? "请阅读满 8 秒" : undefined}
          >
            {countdownDone ? "已阅读" : `${remaining}s 后可关闭`}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
