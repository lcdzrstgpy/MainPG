import { forwardRef, type ReactNode } from "react";
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
