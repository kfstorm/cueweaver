import { MoonIcon, SunIcon } from "@phosphor-icons/react";

import { useTheme } from "./theme-context";

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const dark = theme === "dark";
  const Icon = dark ? MoonIcon : SunIcon;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={className ? `theme-toggle ${className}` : "theme-toggle"}
      onClick={toggleTheme}
    >
      <Icon aria-hidden="true" size={17} weight="regular" />
      <span>Dark mode</span>
      <span className="theme-toggle-state" aria-hidden="true">
        {dark ? "On" : "Off"}
      </span>
    </button>
  );
}
