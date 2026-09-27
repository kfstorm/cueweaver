export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const THEME_STORAGE_KEY = "cueweaver.theme";
const LIGHT_THEME_COLOR = "#f7f8fa";
const DARK_THEME_COLOR = "#111827";

function isThemePreference(
  value: string | null,
): value is Exclude<ThemePreference, "system"> {
  return value === "light" || value === "dark";
}

export function getThemePreference(): ThemePreference {
  if (typeof window === "undefined") return "system";
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

export function getSystemTheme(): ResolvedTheme {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? getSystemTheme() : preference;
}

export function applyTheme(theme: ResolvedTheme): void {
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

export function initializeTheme(): ResolvedTheme {
  const theme = resolveTheme(getThemePreference());
  applyTheme(theme);
  return theme;
}

export { THEME_STORAGE_KEY };
