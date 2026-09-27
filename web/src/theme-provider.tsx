import { useEffect, useState, type ReactNode } from "react";

import { ThemeContext } from "./theme-context";
import {
  applyTheme,
  getThemePreference,
  getSystemTheme,
  THEME_STORAGE_KEY,
  type ThemePreference,
} from "./theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState(getThemePreference);
  const [systemTheme, setSystemTheme] = useState(getSystemTheme);
  const resolvedTheme = preference === "system" ? systemTheme : preference;

  useEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleSystemThemeChange = () => {
      setSystemTheme(getSystemTheme());
    };
    mediaQuery.addEventListener?.("change", handleSystemThemeChange);
    return () => mediaQuery.removeEventListener?.("change", handleSystemThemeChange);
  }, []);

  const setPreference = (nextPreference: ThemePreference) => {
    try {
      if (nextPreference === "system") {
        window.localStorage.removeItem(THEME_STORAGE_KEY);
      } else {
        window.localStorage.setItem(THEME_STORAGE_KEY, nextPreference);
      }
    } catch {
      // Keep the current session usable when browser storage is unavailable.
    }
    setPreferenceState(nextPreference);
  };

  return (
    <ThemeContext value={{ preference, resolvedTheme, setPreference }}>
      {children}
    </ThemeContext>
  );
}
