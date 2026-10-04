import { Link, NavLink, useSearchParams } from "react-router-dom";
import { useI18n } from "./i18n";

export function TermMapNavigation() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const search = params.toString() ? `?${params}` : "";
  return (
    <>
      {params.get("from") === "translate" && (
        <Link className="back-action" to="/translate">
          {t("termMaps.returnTranslate")}
        </Link>
      )}
      <nav className="term-map-navigation" aria-label={t("settings.termMaps")}>
        <NavLink end to={`/settings/term-maps${search}`}>
          {t("settings.termMaps")}
        </NavLink>
        <NavLink to={`/settings/term-maps/automatic${search}`}>
          {t("termMaps.automatic")}
        </NavLink>
      </nav>
    </>
  );
}
