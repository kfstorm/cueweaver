import { NavLink, Outlet } from "react-router-dom";

import { PageHeader } from "./components/page-header";
import { SettingsRow } from "./components/layout";
import { Select } from "./components/ui/input";
import { useI18n } from "./i18n";
import { useTheme } from "./theme-context";

const settingsSections = [
  { label: "settings.general", path: "/settings/general" },
  { label: "settings.modelProfiles", path: "/settings/model-profiles" },
  { label: "settings.termMaps", path: "/settings/term-maps" },
] as const;

export function SettingsNavigation() {
  const { t } = useI18n();
  return (
    <nav className="settings-nav" aria-label={t("settings.navigation")}>
      {settingsSections.map(({ label, path }) => (
        <NavLink
          key={path}
          to={path}
          className={({ isActive }) =>
            isActive ? "settings-nav-link active" : "settings-nav-link"
          }
        >
          {t(label)}
        </NavLink>
      ))}
    </nav>
  );
}

export function SettingsArea() {
  return (
    <>
      <SettingsNavigation />
      <Outlet />
    </>
  );
}

export function GeneralSettingsPage() {
  const { t, locale, setLocale, localeOptions } = useI18n();
  const { preference, setPreference } = useTheme();

  return (
    <>
      <PageHeader title={t("settings.general")} detail={t("settings.generalDetail")} />
      <div className="general-settings">
        <SettingsRow
          id="appearance-title"
          title={t("settings.appearance")}
          description={t("settings.themeHelp")}
        >
          <fieldset className="theme-preference">
            <legend className="sr-only">{t("settings.theme")}</legend>
            {(["system", "light", "dark"] as const).map((value) => (
              <label className="theme-preference-option" key={value}>
                <input
                  type="radio"
                  name="theme-preference"
                  value={value}
                  checked={preference === value}
                  onChange={() => setPreference(value)}
                />
                <span>{t(`theme.${value}`)}</span>
              </label>
            ))}
          </fieldset>
        </SettingsRow>
        <SettingsRow
          id="language-title"
          title={t("language.label")}
          description={t("language.interfaceDetail")}
        >
          <Select
            className="settings-language"
            value={locale}
            aria-labelledby="language-title"
            aria-label={t("language.change")}
            onChange={(event) => setLocale(event.target.value as typeof locale)}
          >
            {localeOptions.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </Select>
        </SettingsRow>
      </div>
    </>
  );
}
