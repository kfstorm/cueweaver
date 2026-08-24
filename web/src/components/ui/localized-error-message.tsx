import { formatError, getErrorDetail, useI18n } from "../../i18n";

export function LocalizedErrorMessage({ error }: { error: unknown }) {
  const { t } = useI18n();
  const message = formatError(error, t);
  const detail = getErrorDetail(error);
  return (
    <>
      <span>{message}</span>
      {detail && detail !== message && (
        <details>
          <summary>{t("jobs.showDiagnostics")}</summary>
          <p>{detail}</p>
        </details>
      )}
    </>
  );
}
