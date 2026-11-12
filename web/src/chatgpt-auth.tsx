import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { SettingsRow } from "./components/layout";
import { PageHeader } from "./components/page-header";
import { Button } from "./components/ui/button";
import { Guidance } from "./components/ui/guidance";
import { useI18n } from "./i18n";

const GUIDE_URL =
  "https://github.com/kfstorm/cueweaver/blob/main/README.md#translate-with-a-chatgpt-subscription";
const SIGN_IN_COMMAND =
  "uv run cueweaver-chatgpt --work-root /absolute/work-root sign-in";
type CredentialStatus = "credentials_present" | "not_configured" | "unavailable";

function CredentialStatus() {
  const { t } = useI18n();
  const query = useQuery({
    queryKey: ["chatgpt-auth-status"],
    retry: false,
    queryFn: async ({ signal }): Promise<CredentialStatus> => {
      const response = await fetch("/api/chatgpt-auth/status", { signal });
      if (!response.ok) throw new Error("Status request failed");
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "status" in body) {
        const status = body.status;
        if (
          status === "credentials_present" ||
          status === "not_configured" ||
          status === "unavailable"
        )
          return status;
      }
      throw new Error("Invalid status response");
    },
  });

  return (
    <div className="chatgpt-status">
      <p role={query.isError ? "alert" : "status"}>
        {t(
          query.isFetching
            ? "chatgpt.loading"
            : query.isError
              ? "chatgpt.error"
              : `chatgpt.${query.data ?? "unavailable"}`,
        )}
      </p>
      <Button
        type="button"
        variant="outline"
        disabled={query.isFetching}
        onClick={() => void query.refetch()}
      >
        {t("chatgpt.refresh")}
      </Button>
    </div>
  );
}

export function ChatGPTProfileGuidance() {
  const { t } = useI18n();
  return (
    <Guidance title={t("chatgpt.title")}>
      <p>{t("chatgpt.profileHelp")}</p>
      <Link className="text-action chatgpt-link" to="/settings/chatgpt">
        {t("chatgpt.instructions")}
      </Link>
    </Guidance>
  );
}

export function ChatGPTPlanStatus() {
  const { t } = useI18n();
  return (
    <Guidance title={t("chatgpt.statusTitle")}>
      <CredentialStatus />
      <p>{t("chatgpt.planHelp")}</p>
      <p>{t("chatgpt.disclaimer")}</p>
    </Guidance>
  );
}

export function ChatGPTSettingsPage() {
  const { t } = useI18n();
  const [copyResult, setCopyResult] = useState<"copied" | "copyFailed">();
  return (
    <>
      <PageHeader title={t("chatgpt.title")} detail={t("chatgpt.detail")} />
      <div className="general-settings">
        <SettingsRow
          id="chatgpt-status-title"
          title={t("chatgpt.statusTitle")}
          description={t("chatgpt.disclaimer")}
        >
          <CredentialStatus />
        </SettingsRow>
        <section className="chatgpt-instructions" aria-labelledby="chatgpt-local-title">
          <h2 id="chatgpt-local-title">{t("chatgpt.localTitle")}</h2>
          <p>{t("chatgpt.localHelp")}</p>
          <pre>
            <code>{SIGN_IN_COMMAND}</code>
          </pre>
          <div className="profile-actions">
            <Button
              type="button"
              variant="outline"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(SIGN_IN_COMMAND);
                  setCopyResult("copied");
                } catch {
                  setCopyResult("copyFailed");
                }
              }}
            >
              {t("chatgpt.copy")}
            </Button>
            {copyResult && <span role="status">{t(`chatgpt.${copyResult}`)}</span>}
          </div>
        </section>
        <section
          className="chatgpt-instructions"
          aria-labelledby="chatgpt-remote-title"
        >
          <h2 id="chatgpt-remote-title">{t("chatgpt.remoteTitle")}</h2>
          <p>{t("chatgpt.remoteHelp")}</p>
          <a
            className="text-action chatgpt-link"
            href={GUIDE_URL}
            target="_blank"
            rel="noreferrer"
          >
            {t("chatgpt.guide")}
          </a>
        </section>
        <section
          className="chatgpt-instructions"
          aria-labelledby="chatgpt-recovery-title"
        >
          <h2 id="chatgpt-recovery-title">{t("chatgpt.recoveryTitle")}</h2>
          <p>{t("chatgpt.recoveryHelp")}</p>
          <Link className="text-action chatgpt-link" to="/settings/model-profiles">
            {t("settings.manageModelProfiles")}
          </Link>
        </section>
      </div>
    </>
  );
}
