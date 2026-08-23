import type { ReactNode } from "react";

import { ThemeToggle } from "../theme-toggle";

export function PageHeader({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      <div className="page-header-actions">
        {children}
        <ThemeToggle className="page-theme-toggle" />
      </div>
    </header>
  );
}
