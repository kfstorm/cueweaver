import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

type GuidanceTone = "info" | "warning" | "error" | "success";

export function Guidance({
  title,
  children,
  tone = "info",
  action,
  className,
  role,
}: {
  title: string;
  children: ReactNode;
  tone?: GuidanceTone;
  action?: ReactNode;
  className?: string;
  role?: "alert" | "status";
}) {
  return (
    <div className={cn("guidance", `guidance-${tone}`, className)} role={role}>
      <div className="guidance-copy">
        <strong>{title}</strong>
        <div>{children}</div>
      </div>
      {action && <div className="guidance-action">{action}</div>}
    </div>
  );
}

export function QuickStart({
  steps,
  title,
  className,
}: {
  steps: string[];
  title: string;
  className?: string;
}) {
  return (
    <section className={cn("quick-start", className)} aria-label={title}>
      <strong>{title}</strong>
      <ol>
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </section>
  );
}
