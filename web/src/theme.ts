export type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "cueweaver.theme";
const LIGHT_THEME_COLOR = "#f7f8fa";
const DARK_THEME_COLOR = "#111827";

function isTheme(value: string | null): value is Theme {
  return value === "light" || value === "dark";
}

export function getStoredTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function getSystemTheme(): Theme {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function getInitialTheme(): Theme {
  return getStoredTheme() ?? getSystemTheme();
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  const themeColor =
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]') ??
    document.head.appendChild(document.createElement("meta"));
  themeColor.setAttribute("name", "theme-color");
  themeColor.setAttribute(
    "content",
    theme === "dark" ? DARK_THEME_COLOR : LIGHT_THEME_COLOR,
  );
}

export function initializeTheme(): Theme {
  const theme = getInitialTheme();
  applyTheme(theme);
  return theme;
}

export { THEME_STORAGE_KEY };
