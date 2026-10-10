import type { ReactNode } from "react";

import { cn } from "../lib/utils";

export function SectionToolbar({
  children,
  actions,
  className,
}: {
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("section-toolbar", className)}>
      <div className="toolbar-content">{children}</div>
      {actions && <div className="toolbar-actions">{actions}</div>}
    </div>
  );
}

export function Workflow({ children }: { children: ReactNode }) {
  return <div className="workflow">{children}</div>;
}

export function WorkflowStep({
  id,
  index,
  title,
  muted,
  children,
}: {
  id: string;
  index: string;
  title: string;
  muted?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={cn("workflow-panel", muted && "muted")} aria-labelledby={id}>
      <div className="step-index" aria-hidden="true">
        {index}
      </div>
      <div className="step-content">
        <h2 id={id}>{title}</h2>
        {children}
      </div>
    </section>
  );
}

export function SettingsRow({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="settings-row" aria-labelledby={id}>
      <div>
        <h2 id={id}>{title}</h2>
        <p className="field-help">{description}</p>
      </div>
      <div className="settings-row-control">{children}</div>
    </section>
  );
}

export function FormField({
  id,
  label,
  help,
  children,
  className,
}: {
  id: string;
  label: ReactNode;
  help?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("form-field", className)}>
      <label htmlFor={id}>{label}</label>
      {children}
      {help && (
        <p id={`${id}-help`} className="field-help">
          {help}
        </p>
      )}
    </div>
  );
}
