import type { ReactNode } from "react";

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
      {children && <div className="page-header-actions">{children}</div>}
    </header>
  );
}
