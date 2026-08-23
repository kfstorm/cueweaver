import { MoonIcon, SunIcon } from "@phosphor-icons/react";

import { useI18n } from "./i18n";
import { useTheme } from "./theme-context";

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const { t } = useI18n();
  const dark = theme === "dark";
  const Icon = dark ? MoonIcon : SunIcon;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      className={className ? `theme-toggle ${className}` : "theme-toggle"}
      onClick={toggleTheme}
    >
      <Icon aria-hidden="true" size={17} weight="regular" />
      <span>{t("theme.darkMode")}</span>
      <span className="theme-toggle-state" aria-hidden="true">
        {dark ? t("theme.on") : t("theme.off")}
      </span>
    </button>
  );
}
