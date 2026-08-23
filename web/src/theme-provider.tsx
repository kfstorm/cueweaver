import { useEffect, useRef, useState, type ReactNode } from "react";

import { ThemeContext } from "./theme-context";
import {
  applyTheme,
  getInitialTheme,
  getStoredTheme,
  getSystemTheme,
  THEME_STORAGE_KEY,
} from "./theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState(getInitialTheme);
  const userSelectedTheme = useRef(false);

  useEffect(() => {
    applyTheme(theme);

    if (typeof window.matchMedia !== "function") return;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleSystemThemeChange = () => {
      if (!userSelectedTheme.current && getStoredTheme() === null) {
        setTheme(getSystemTheme());
      }
    };
    mediaQuery.addEventListener?.("change", handleSystemThemeChange);
    return () => mediaQuery.removeEventListener?.("change", handleSystemThemeChange);
  }, [theme]);

  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    userSelectedTheme.current = true;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch {
      // Keep the current session usable when browser storage is unavailable.
    }
    setTheme(nextTheme);
  };

  return <ThemeContext value={{ theme, toggleTheme }}>{children}</ThemeContext>;
}
