import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn("form-control", className)} {...props} />;
}

export function Textarea({
  className,
  monospace = false,
  ...props
}: ComponentProps<"textarea"> & { monospace?: boolean }) {
  return (
    <textarea
      className={cn(
        "form-control",
        "textarea-control",
        monospace && "code-control",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn("select-control", className)} {...props} />;
}
