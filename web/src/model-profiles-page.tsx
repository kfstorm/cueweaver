import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "./components/page-header";
import { Button } from "./components/ui/button";
import { Input, Select, Textarea } from "./components/ui/input";
import { useI18n } from "./i18n";
import {
  requestModelProfileOptions,
  useDeleteModelProfile,
  useModelProfileReference,
  useModelProfiles,
  useSaveModelProfile,
  type ModelProfile,
  type ModelProfileOption,
  type ProfileInput,
} from "./model-profiles";

const PROVIDER_DOCS_URL =
  "https://github.com/machinewrapped/llm-subtrans#translation-providers";

const empty = (): ProfileInput => ({ name: "", provider: "", settings: [] });

export function ModelProfilesPage() {
  const { t } = useI18n();
  const profiles = useModelProfiles();
  const remove = useDeleteModelProfile();
  const navigate = useNavigate();

  return (
    <>
      <PageHeader
        title={t("settings.modelProfiles")}
        detail={t("modelProfiles.managementDetail")}
      />
      <Button asChild>
        <Link to="/settings/model-profiles/new">{t("modelProfiles.create")}</Link>
      </Button>
      {profiles.isPending && <p role="status">{t("modelProfiles.loading")}</p>}
      {profiles.isError && <p role="alert">{profiles.error.message}</p>}
      {profiles.data?.length === 0 && <p>{t("modelProfiles.empty")}</p>}
      <div className="profile-list">
        {profiles.data?.map((profile) => (
          <article key={profile.id} className="profile-row">
            <div>
              <strong>{profile.name}</strong>
              <p className="field-help">
                {t("modelProfiles.provider", { value: profile.provider })}
              </p>
            </div>
            <div className="profile-actions">
              <Link to={`/settings/model-profiles/${profile.id}`}>
                {t("modelProfiles.edit")}
              </Link>
              <Button
                type="button"
                variant="outline"
                disabled={remove.isPending || !profile.deletable}
                onClick={() =>
                  remove.mutate(profile.id, {
                    onSuccess: () => navigate("/settings/model-profiles"),
                  })
                }
              >
                {t("common.delete")}
              </Button>
            </div>
          </article>
        ))}
      </div>
      {remove.isError && <p role="alert">{remove.error.message}</p>}
    </>
  );
}

export function ModelProfileEditor() {
  const { t } = useI18n();
  const { profileId } = useParams();
  const navigate = useNavigate();
  const profiles = useModelProfiles();
  const reference = useModelProfileReference();
  const save = useSaveModelProfile();
  const current = profiles.data?.find((item) => item.id === profileId);

  if (profiles.isPending) return <p role="status">{t("modelProfiles.loading")}</p>;
  if (profiles.isError) return <p role="alert">{profiles.error.message}</p>;
  if (profileId !== "new" && !current)
    return <p role="alert">{t("modelProfiles.notFound")}</p>;

  return (
    <ProfileForm
      key={current?.id ?? "new"}
      current={current}
      initial={
        current
          ? {
              name: current.name,
              provider: current.provider,
              settings: current.settings,
            }
          : empty()
      }
      providers={reference.data?.providers ?? []}
      referencePending={reference.isPending}
      referenceError={reference.isError ? reference.error.message : undefined}
      retryReference={() => void reference.refetch()}
      onSave={(input) =>
        save.mutate(
          { id: current?.id, input },
          { onSuccess: () => navigate("/settings/model-profiles") },
        )
      }
      pending={save.isPending}
      error={save.error?.message}
    />
  );
}

function ProfileForm({
  current,
  initial,
  providers,
  referencePending,
  referenceError,
  retryReference,
  onSave,
  pending,
  error,
}: {
  current?: ModelProfile;
  initial: ProfileInput;
  providers: string[];
  referencePending: boolean;
  referenceError?: string;
  retryReference: () => void;
  onSave: (input: ProfileInput) => void;
  pending: boolean;
  error?: string;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(initial);
  const [options, setOptions] = useState<ModelProfileOption[]>([]);
  const [refreshKeys, setRefreshKeys] = useState<string[]>([]);
  const [optionsPending, setOptionsPending] = useState(Boolean(initial.provider));
  const [optionsError, setOptionsError] = useState<string>();
  const requestId = useRef(0);
  const draftRevision = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const initialRequest = useRef(initial);

  const settingsRecord = useCallback(
    () => Object.fromEntries(draft.settings.map(({ key, value }) => [key, value])),
    [draft.settings],
  );

  const refresh = useCallback(
    async (provider: string, settings: Record<string, unknown>) => {
      if (!provider) return;
      controller.current?.abort();
      const nextController = new AbortController();
      controller.current = nextController;
      const id = ++requestId.current;
      const revision = draftRevision.current;
      setOptionsPending(true);
      setOptionsError(undefined);
      try {
        const result = await requestModelProfileOptions(
          provider,
          settings,
          nextController.signal,
        );
        if (id !== requestId.current || revision !== draftRevision.current) return;
        const visible = new Set(result.options.map((option) => option.key));
        setDraft((previous) => {
          if (revision !== draftRevision.current) return previous;
          return {
            ...previous,
            settings: [
              ...previous.settings.filter(
                (setting) =>
                  visible.has(setting.key) && !(setting.key in result.setting_updates),
              ),
              ...Object.entries(result.setting_updates).map(([key, value]) => ({
                key,
                value,
              })),
            ],
          };
        });
        setOptions(result.options);
        setRefreshKeys(result.refresh_when_changed);
      } catch (caught) {
        if (id !== requestId.current || nextController.signal.aborted) return;
        setOptionsError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (id === requestId.current) setOptionsPending(false);
      }
    },
    [],
  );

  useEffect(() => {
    const { provider, settings } = initialRequest.current;
    if (!provider) return;
    void refresh(
      provider,
      Object.fromEntries(settings.map(({ key, value }) => [key, value])),
    );
    return () => controller.current?.abort();
  }, [refresh]);

  const explicit = new Map(
    draft.settings.map((setting) => [setting.key, setting.value]),
  );
  const setSetting = (key: string, value: unknown) => {
    draftRevision.current += 1;
    setDraft((previous) => ({
      ...previous,
      settings: [
        ...previous.settings.filter((setting) => setting.key !== key),
        { key, value },
      ],
    }));
  };
  const clearSetting = (key: string) => {
    draftRevision.current += 1;
    setDraft((previous) => ({
      ...previous,
      settings: previous.settings.filter((setting) => setting.key !== key),
    }));
  };
  const refreshAfterChange = (key: string, value?: unknown) => {
    if (!refreshKeys.includes(key)) return;
    const settings = settingsRecord();
    if (value === undefined || value === "") delete settings[key];
    else settings[key] = value;
    void refresh(draft.provider, settings);
  };
  const changeProvider = (provider: string) => {
    if (
      draft.settings.length > 0 &&
      !window.confirm(t("modelProfiles.providerChangeConfirm"))
    )
      return;
    draftRevision.current += 1;
    controller.current?.abort();
    ++requestId.current;
    setOptions([]);
    setRefreshKeys([]);
    setOptionsError(undefined);
    setDraft((previous) => ({ ...previous, provider, settings: [] }));
    void refresh(provider, {});
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!pending) onSave(draft);
  };

  return (
    <>
      <PageHeader
        title={
          current
            ? t("modelProfiles.editTitle", { name: current.name })
            : t("modelProfiles.create")
        }
        detail={t("modelProfiles.formDetail")}
      />
      <form onSubmit={submit} className="profile-form">
        <label>
          {t("modelProfiles.name")}
          <Input
            required
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
        </label>
        <label>
          <span className="profile-label-heading">
            {t("modelProfiles.providerValue")}
            <a href={PROVIDER_DOCS_URL} target="_blank" rel="noreferrer">
              {t("modelProfiles.providerDocs")}
            </a>
          </span>
          <Select
            required
            value={draft.provider}
            disabled={referencePending || !!referenceError}
            onChange={(event) => changeProvider(event.target.value)}
          >
            <option value="" disabled>
              {t("modelProfiles.chooseProvider")}
            </option>
            {providers.map((provider) => (
              <option value={provider} key={provider}>
                {provider}
              </option>
            ))}
          </Select>
        </label>
        {referencePending && <p role="status">{t("modelProfiles.referenceLoading")}</p>}
        {referenceError && (
          <div className="field-recovery">
            <p role="alert" className="form-error">
              {t("modelProfiles.referenceError")}: {referenceError}
            </p>
            <Button type="button" variant="outline" onClick={retryReference}>
              {t("common.tryAgain")}
            </Button>
          </div>
        )}
        {draft.provider && (
          <section className="profile-options" aria-labelledby="profile-options-title">
            <h2 id="profile-options-title">{t("modelProfiles.settings")}</h2>
            {options.map((option) => (
              <OptionField
                key={option.key}
                option={option}
                explicitValue={explicit.get(option.key)}
                isExplicit={explicit.has(option.key)}
                onChange={(value) => setSetting(option.key, value)}
                onClear={() => {
                  clearSetting(option.key);
                  refreshAfterChange(option.key);
                }}
                onRefresh={(value) => refreshAfterChange(option.key, value)}
              />
            ))}
            {optionsPending && (
              <p className="field-help" role="status">
                {t("modelProfiles.optionsLoading")}
              </p>
            )}
            {optionsError && (
              <div className="field-recovery">
                <p role="alert" className="form-error">
                  {t("modelProfiles.optionsError")}: {optionsError}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void refresh(draft.provider, settingsRecord())}
                >
                  {t("modelProfiles.retryOptions")}
                </Button>
              </div>
            )}
          </section>
        )}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="profile-actions">
          <Button type="submit" disabled={pending || !draft.provider}>
            {t(pending ? "modelProfiles.saving" : "modelProfiles.save")}
          </Button>
          <Link to="/settings/model-profiles">{t("common.cancel")}</Link>
        </div>
      </form>
    </>
  );
}

function OptionField({
  option,
  explicitValue,
  isExplicit,
  onChange,
  onClear,
  onRefresh,
}: {
  option: ModelProfileOption;
  explicitValue: unknown;
  isExplicit: boolean;
  onChange: (value: unknown) => void;
  onClear: () => void;
  onRefresh: (value?: unknown) => void;
}) {
  const { t } = useI18n();
  const displayed = isExplicit ? explicitValue : option.value;
  const clearEmpty = (value: string) => {
    if (value === "") onClear();
    else
      onChange(
        option.type === "integer"
          ? Number.parseInt(value, 10)
          : option.type === "number"
            ? Number(value)
            : value,
      );
  };
  const refreshOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onRefresh(isExplicit ? explicitValue : undefined);
    }
  };

  return (
    <div className="profile-option">
      <label htmlFor={`profile-option-${option.key}`}>{option.key}</label>
      {option.description && <p className="field-help">{option.description}</p>}
      <div className="profile-option-control">
        {option.type === "boolean" ? (
          <input
            id={`profile-option-${option.key}`}
            type="checkbox"
            checked={Boolean(displayed)}
            onChange={(event) => {
              onChange(event.target.checked);
              onRefresh(event.target.checked);
            }}
          />
        ) : option.type === "choice" ? (
          <Select
            id={`profile-option-${option.key}`}
            value={String(displayed ?? "")}
            onChange={(event) => {
              onChange(event.target.value);
              onRefresh(event.target.value);
            }}
          >
            {(option.choices ?? []).map((choice) => (
              <option value={choice} key={choice}>
                {choice}
              </option>
            ))}
          </Select>
        ) : option.type === "multiline" ? (
          <Textarea
            id={`profile-option-${option.key}`}
            value={String(displayed ?? "")}
            onChange={(event) => clearEmpty(event.target.value)}
            onBlur={() => onRefresh(isExplicit ? explicitValue : undefined)}
          />
        ) : (
          <Input
            id={`profile-option-${option.key}`}
            type={option.type === "string" ? "text" : "number"}
            step={
              option.type === "integer"
                ? "1"
                : option.type === "number"
                  ? "any"
                  : undefined
            }
            value={String(displayed ?? "")}
            onChange={(event) => clearEmpty(event.target.value)}
            onBlur={() => onRefresh(isExplicit ? explicitValue : undefined)}
            onKeyDown={refreshOnEnter}
          />
        )}
        {isExplicit && (
          <Button type="button" variant="outline" onClick={onClear}>
            {t("modelProfiles.useDefault")}
          </Button>
        )}
      </div>
      {!isExplicit && <p className="field-help">{t("modelProfiles.runtimeDefault")}</p>}
    </div>
  );
}
