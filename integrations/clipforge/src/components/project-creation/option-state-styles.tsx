import { LuCheck } from "react-icons/lu";
import { cn } from "@/lib/utils";

const INTERACTION = "border transition-all duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-40";

/** Shared selected/unselected treatment for compact, pill-shaped option controls. */
export function optionChipClass(selected: boolean) {
  return cn(
    "relative inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold",
    INTERACTION,
    selected
      ? "border-primary bg-primary text-primary-foreground shadow-sm shadow-primary/35 scale-[1.02]"
      : "border-border/50 bg-muted/20 text-foreground hover:border-primary/50 hover:bg-primary/5"
  );
}

/** Shared selected/unselected treatment for larger option cards and segmented choices. */
export function optionCardClass(selected: boolean) {
  return cn(
    "relative",
    INTERACTION,
    selected
      ? "border-primary bg-primary text-primary-foreground shadow-md shadow-primary/25 scale-[1.01]"
      : "border-border/50 bg-muted/20 text-foreground hover:border-primary/50 hover:bg-primary/5"
  );
}

/** Decorative confirmation marker; semantics remain on the parent button's ARIA state. */
export function SelectionCheck({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-selection-check
      className={cn("inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-primary-foreground/20 text-primary-foreground", className)}
    >
      <LuCheck className="size-3 stroke-[3]" />
    </span>
  );
}
