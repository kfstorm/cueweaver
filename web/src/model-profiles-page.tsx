import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "./components/page-header";
import { Button } from "./components/ui/button";
import { Input, Select, Textarea } from "./components/ui/input";
import { useI18n } from "./i18n";
import {
  useDeleteModelProfile,
  useModelProfileReference,
  useModelProfiles,
  useSaveModelProfile,
  type ModelProfile,
  type ModelProfileReference,
  type ProfileInput,
  type ProfileSetting,
  type SettingReference,
} from "./model-profiles";

const empty = (parent_id: string | null = null): ProfileInput => ({
  name: "",
  parent_id,
  selectable: false,
  settings: [],
});
const valueType = (entry: ProfileSetting): string =>
  entry.kind === "unset"
    ? "unset"
    : Array.isArray(entry.value)
      ? "string list"
      : entry.value !== null && typeof entry.value === "object"
        ? "JSON"
        : typeof entry.value === "number"
          ? Number.isInteger(entry.value)
            ? "integer"
            : "number"
          : typeof entry.value;
const displayValue = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value);
const inputValue = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value);
const referenceTypeLabel = (type: string | null | undefined): string | undefined =>
  type === "array" ? "string list" : (type ?? undefined);
const PROVIDER_REFERENCE: SettingReference = {
  key: "provider",
  type: "string",
  description: null,
  choices: null,
};
const settingTypeInfo = (
  entry: ProfileSetting,
  reference: SettingReference | undefined,
  referenceControlled: boolean,
) => {
  const actualType = valueType(entry);
  const referenceType = referenceTypeLabel(reference?.type);
  const matchesReferenceType =
    !referenceType ||
    actualType === referenceType ||
    (referenceType === "number" && actualType === "integer");
  const lockTypeToReference = Boolean(referenceType && referenceControlled);
  return { actualType, referenceType, matchesReferenceType, lockTypeToReference };
};
const matchesSettingReference = (
  value: unknown,
  reference: SettingReference,
): boolean => {
  if (!matchesSettingReferenceType(value, reference)) return false;
  return (
    !reference.choices?.length ||
    (typeof value === "string" && reference.choices.includes(value))
  );
};
const matchesSettingReferenceType = (
  value: unknown,
  reference: SettingReference,
): boolean =>
  reference.type === "string"
    ? typeof value === "string"
    : reference.type === "integer"
      ? typeof value === "number" && Number.isInteger(value)
      : reference.type === "number"
        ? typeof value === "number" && Number.isFinite(value)
        : reference.type === "boolean"
          ? typeof value === "boolean"
          : reference.type === "array"
            ? Array.isArray(value) && value.every((item) => typeof item === "string")
            : true;

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
                {t(
                  profile.selectable
                    ? "modelProfiles.selectable"
                    : "modelProfiles.base",
                )}{" "}
                ·{" "}
                {t("modelProfiles.parent", {
                  name:
                    profiles.data.find((item) => item.id === profile.parent_id)?.name ??
                    t("modelProfiles.none"),
                })}
              </p>
              <p className="field-help">
                {t("modelProfiles.provider", {
                  value: displayValue(
                    profile.effective_settings.find((item) => item.key === "provider")
                      ?.value ?? "—",
                  ),
                })}{" "}
                ·{" "}
                {t("modelProfiles.model", {
                  value: displayValue(
                    profile.effective_settings.find((item) => item.key === "model")
                      ?.value ?? "—",
                  ),
                })}
              </p>
            </div>
            <div className="profile-actions">
              <Link to={`/settings/model-profiles/${profile.id}`}>
                {t("modelProfiles.edit")}
              </Link>
              <Link
                to={`/settings/model-profiles/new?parent=${encodeURIComponent(profile.id)}`}
              >
                {t("modelProfiles.createDerived")}
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
  const location = useLocation();
  const navigate = useNavigate();
  const profiles = useModelProfiles();
  const reference = useModelProfileReference();
  const save = useSaveModelProfile();
  const current = profiles.data?.find((item) => item.id === profileId);
  const parent = new URLSearchParams(location.search).get("parent");
  if (profiles.isPending) return <p role="status">{t("modelProfiles.loading")}</p>;
  if (profiles.isError) return <p role="alert">{profiles.error.message}</p>;
  if (profileId !== "new" && !current)
    return <p role="alert">{t("modelProfiles.notFound")}</p>;
  return (
    <ProfileForm
      key={current?.id ?? parent ?? "new"}
      current={current}
      initial={
        current
          ? {
              name: current.name,
              parent_id: current.parent_id,
              selectable: current.selectable,
              settings: current.settings,
            }
          : empty(profiles.data?.some((item) => item.id === parent) ? parent : null)
      }
      profiles={profiles.data ?? []}
      reference={reference.data}
      referencePending={reference.isPending}
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
  profiles,
  reference,
  referencePending,
  onSave,
  pending,
  error,
}: {
  current?: ModelProfile;
  initial: ProfileInput;
  profiles: ModelProfile[];
  reference?: ModelProfileReference;
  referencePending: boolean;
  onSave: (input: ProfileInput) => void;
  pending: boolean;
  error?: string;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(initial);
  const [newKey, setNewKey] = useState("");
  const [parseErrors, setParseErrors] = useState<Map<string, string>>(() => new Map());
  const [referenceControlledProviders, setReferenceControlledProviders] = useState<
    Map<string, string | null>
  >(() => new Map());
  const setParseError = useCallback((key: string, message: string) => {
    setParseErrors((previous) => {
      const next = new Map(previous);
      if (message) next.set(key, message);
      else next.delete(key);
      return next;
    });
  }, []);
  const disallowed = useMemo(() => {
    const descendants = new Set<string>(current ? [current.id] : []);
    for (let i = 0; i < profiles.length; i++)
      for (const profile of profiles)
        if (profile.parent_id && descendants.has(profile.parent_id))
          descendants.add(profile.id);
    return descendants;
  }, [current, profiles]);
  const parentEffective =
    profiles.find((item) => item.id === draft.parent_id)?.effective_settings ?? [];
  const inherited = new Map(parentEffective.map((item) => [item.key, item]));
  const local = new Map(draft.settings.map((item) => [item.key, item]));
  const keys = [...new Set([...inherited.keys(), ...local.keys()])].sort();
  const localProvider = local.get("provider");
  const providerValue =
    localProvider?.kind === "unset"
      ? undefined
      : localProvider?.kind === "literal"
        ? localProvider.value
        : inherited.get("provider")?.value;
  const effectiveProvider =
    typeof providerValue === "string" ? providerValue : undefined;
  const providerNames = Object.keys(reference?.providers ?? {});
  const providerSettings = effectiveProvider
    ? (reference?.providers?.[effectiveProvider] ?? [])
    : [];
  const activeReferenceControlledKeys = new Set(
    [...referenceControlledProviders]
      .filter(([key, provider]) => {
        const settingReference =
          key === "provider"
            ? PROVIDER_REFERENCE
            : providerSettings.find((item) => item.key === key);
        return (
          Boolean(settingReference) &&
          provider === (key === "provider" ? null : effectiveProvider)
        );
      })
      .map(([key]) => key),
  );
  const addableSettings = providerSettings.filter((item) => !keys.includes(item.key));
  if (!effectiveProvider && !keys.includes("provider")) {
    addableSettings.unshift({
      key: "provider",
      type: "string",
      description: null,
      choices: null,
    });
  }
  const selectedReference = addableSettings.find((item) => item.key === newKey.trim());
  const validationErrors = new Map(parseErrors);
  for (const key of activeReferenceControlledKeys) {
    const setting = local.get(key);
    const settingReference =
      key === "provider"
        ? PROVIDER_REFERENCE
        : providerSettings.find((item) => item.key === key);
    if (
      setting?.kind === "literal" &&
      settingReference &&
      !matchesSettingReference(setting.value, settingReference)
    ) {
      validationErrors.set(
        key,
        settingReference.choices?.length &&
          matchesSettingReferenceType(setting.value, settingReference)
          ? t("modelProfiles.selectSuggestedValue", { key })
          : t("modelProfiles.invalidValue", {
              type:
                referenceTypeLabel(settingReference.type) ??
                settingReference.type ??
                "string",
              key,
            }),
      );
    }
  }
  const change = (entry: ProfileSetting) => {
    if (entry.kind === "unset") setParseError(entry.key, "");
    setDraft((previous) => ({
      ...previous,
      settings: [...previous.settings.filter((item) => item.key !== entry.key), entry],
    }));
  };
  const remove = (key: string) => {
    setParseError(key, "");
    setReferenceControlledProviders((previous) => {
      const next = new Map(previous);
      next.delete(key);
      return next;
    });
    setDraft((previous) => ({
      ...previous,
      settings: previous.settings.filter((item) => item.key !== key),
    }));
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSave(draft);
  };
  const addSetting = () => {
    const key = newKey.trim();
    if (!key || keys.includes(key)) return;
    change({ key, kind: "literal", value: "" });
    if (providerSettings.some((item) => item.key === key)) {
      setReferenceControlledProviders((previous) =>
        new Map(previous).set(key, effectiveProvider ?? null),
      );
    } else if (referencePending && effectiveProvider) {
      setReferenceControlledProviders((previous) =>
        new Map(previous).set(key, effectiveProvider),
      );
    }
    setNewKey("");
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
          {t("modelProfiles.parentField")}
          <Select
            value={draft.parent_id ?? ""}
            onChange={(event) =>
              setDraft({ ...draft, parent_id: event.target.value || null })
            }
          >
            <option value="">{t("modelProfiles.none")}</option>
            {profiles
              .filter((item) => !disallowed.has(item.id))
              .map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name}
                </option>
              ))}
          </Select>
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={!draft.selectable}
            onChange={(event) =>
              setDraft({ ...draft, selectable: !event.target.checked })
            }
          />
          {t("modelProfiles.baseControl")}
        </label>
        <h2>{t("modelProfiles.settings")}</h2>
        <p className="field-help">{t("modelProfiles.removeHelp")}</p>
        {keys.map((key) => {
          const entry = local.get(key);
          const parentEntry = inherited.get(key);
          const settingReference =
            key === "provider"
              ? PROVIDER_REFERENCE
              : providerSettings.find((item) => item.key === key);
          const referenceControlled =
            activeReferenceControlledKeys.has(key) && Boolean(settingReference);
          const typeEntry =
            entry?.kind === "literal"
              ? entry
              : !entry && parentEntry
                ? { key, kind: "literal" as const, value: parentEntry.value }
                : undefined;
          const typeInfo = typeEntry
            ? settingTypeInfo(typeEntry, settingReference, referenceControlled)
            : undefined;
          const effective =
            entry?.kind === "unset"
              ? undefined
              : entry?.kind === "literal"
                ? entry.value
                : parentEntry?.value;
          return (
            <div className="profile-setting" key={key}>
              <div>
                <strong>{key}</strong>{" "}
                <span className="field-help">
                  {entry
                    ? entry.kind === "unset"
                      ? t("modelProfiles.unsetLocally")
                      : t("modelProfiles.local")
                    : t("modelProfiles.inheritedFrom", {
                        name: parentEntry?.source.name ?? "",
                      })}{" "}
                  ·{" "}
                  {typeInfo
                    ? typeInfo.lockTypeToReference
                      ? typeInfo.referenceType
                      : typeInfo.actualType
                    : entry?.kind === "unset"
                      ? (referenceTypeLabel(settingReference?.type) ?? valueType(entry))
                      : ""}
                </span>
                {settingReference?.description && (
                  <p className="field-help profile-setting-description">
                    {settingReference.description}
                  </p>
                )}
                {typeInfo?.referenceType &&
                  !typeInfo.matchesReferenceType &&
                  !referenceControlled && (
                    <p className="field-help">
                      {t("modelProfiles.expectedType", {
                        type: typeInfo.referenceType,
                      })}
                    </p>
                  )}
              </div>
              <span className="profile-value">
                {effective === undefined
                  ? t("modelProfiles.unset")
                  : displayValue(effective)}
              </span>
              {entry ? (
                <>
                  <Button type="button" variant="outline" onClick={() => remove(key)}>
                    {t("modelProfiles.removeLocal")}
                  </Button>
                  {entry.kind !== "unset" && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => change({ key, kind: "unset", value: null })}
                    >
                      {t("modelProfiles.unset")}
                    </Button>
                  )}
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      change({ key, kind: "literal", value: parentEntry?.value ?? "" })
                    }
                  >
                    {t("modelProfiles.override")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => change({ key, kind: "unset", value: null })}
                  >
                    {t("modelProfiles.unset")}
                  </Button>
                </>
              )}
              {entry?.kind === "literal" && (
                <LiteralEditor
                  key={`${key}:${JSON.stringify([
                    settingReference?.type ?? null,
                    settingReference?.choices ?? null,
                    key === "provider" &&
                      providerNames.length > 0 &&
                      typeInfo?.actualType === "string",
                  ])}`}
                  entry={entry}
                  reference={settingReference}
                  referenceControlled={referenceControlled}
                  providerNames={providerNames}
                  onChange={change}
                  onTypeChange={(type) => {
                    if (type === referenceTypeLabel(settingReference?.type)) {
                      setReferenceControlledProviders((previous) =>
                        new Map(previous).set(
                          key,
                          key === "provider" ? null : (effectiveProvider ?? null),
                        ),
                      );
                    }
                  }}
                  onError={setParseError}
                />
              )}
            </div>
          );
        })}
        <div className="profile-add">
          <label>
            {t("modelProfiles.newKey")}
            <Input
              list="model-profile-setting-reference"
              autoComplete="off"
              value={newKey}
              onChange={(event) => setNewKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addSetting();
                }
              }}
              aria-describedby="model-profile-setting-help"
            />
            <datalist id="model-profile-setting-reference">
              {addableSettings.map((item) => (
                <option
                  key={item.key}
                  value={item.key}
                  label={`${item.type ?? ""}${item.description ? ` · ${item.description}` : ""}`}
                />
              ))}
            </datalist>
            {selectedReference && (
              <span className="field-help" role="status">
                {selectedReference.type ?? t("modelProfiles.valueType")}
                {selectedReference.description
                  ? ` · ${selectedReference.description}`
                  : ""}
              </span>
            )}
            <span id="model-profile-setting-help" className="field-help">
              {t("modelProfiles.settingSuggestionHelp")}
            </span>
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={!newKey.trim() || keys.includes(newKey.trim())}
            onClick={addSetting}
          >
            {t("modelProfiles.addSetting")}
          </Button>
        </div>
        {[...validationErrors].map(([key, message]) => (
          <p key={key} role="alert" className="form-error">
            {message}
          </p>
        ))}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="profile-actions">
          <Button type="submit" disabled={pending || validationErrors.size > 0}>
            {t(pending ? "modelProfiles.saving" : "modelProfiles.save")}
          </Button>
          <Link to="/settings/model-profiles">{t("common.cancel")}</Link>
        </div>
      </form>
    </>
  );
}

function LiteralEditor({
  entry,
  reference,
  referenceControlled,
  providerNames,
  onChange,
  onTypeChange,
  onError,
}: {
  entry: ProfileSetting;
  reference?: SettingReference;
  referenceControlled: boolean;
  providerNames: string[];
  onChange: (entry: ProfileSetting) => void;
  onTypeChange: (type: string) => void;
  onError: (key: string, message: string) => void;
}) {
  const { t } = useI18n();
  const typeInfo = settingTypeInfo(entry, reference, referenceControlled);
  const isKnownProvider =
    entry.key === "provider" &&
    providerNames.length > 0 &&
    typeInfo.actualType === "string";
  const type =
    typeInfo.lockTypeToReference && typeInfo.referenceType
      ? typeInfo.referenceType
      : typeInfo.actualType;
  const [raw, setRaw] = useState(inputValue(entry.value));
  const [editorType, setEditorType] = useState(type);
  const choicesHelpId = `model-profile-${entry.key}-choices-help`;
  const editorSchemaKey = JSON.stringify([
    reference?.type ?? null,
    reference?.choices ?? null,
    isKnownProvider,
  ]);
  useEffect(() => {
    onError(entry.key, "");
  }, [editorSchemaKey, entry.key, onError]);
  const changeType = (next: string) => {
    setEditorType(next);
    onError(entry.key, "");
    onTypeChange(next);
    const value =
      next === "boolean"
        ? false
        : next === "integer" || next === "number"
          ? 0
          : next === "string list"
            ? []
            : next === "JSON"
              ? {}
              : "";
    setRaw(inputValue(value));
    onChange({ ...entry, value });
  };
  const changeRaw = (text: string) => {
    setRaw(text);
    try {
      let value: unknown = text;
      if (editorType === "integer" || editorType === "number") {
        value = Number(text);
        if (
          !text.trim() ||
          !Number.isFinite(value) ||
          (editorType === "integer" && !Number.isInteger(value))
        )
          throw new Error("Enter a valid number");
      } else if (editorType === "string list" || editorType === "JSON") {
        value = JSON.parse(text);
        if (
          editorType === "string list" &&
          (!Array.isArray(value) || !value.every((item) => typeof item === "string"))
        )
          throw new Error("Enter a JSON array of strings");
        if (
          editorType === "JSON" &&
          (value === null || typeof value !== "object" || Array.isArray(value))
        )
          throw new Error("Enter a JSON object");
      }
      onError(entry.key, "");
      onChange({ ...entry, value });
    } catch {
      onError(
        entry.key,
        t("modelProfiles.invalidValue", { type: editorType, key: entry.key }),
      );
    }
  };
  return (
    <div className="profile-editor">
      <label>
        {t("modelProfiles.valueType")}
        {isKnownProvider ? (
          <Select value="string" disabled>
            <option value="string">string</option>
          </Select>
        ) : (
          <Select
            value={editorType}
            disabled={typeInfo.lockTypeToReference}
            onChange={(event) => changeType(event.target.value)}
          >
            {["string", "integer", "number", "boolean", "string list", "JSON"].map(
              (item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ),
            )}
          </Select>
        )}
      </label>
      {isKnownProvider ? (
        <label>
          {t("modelProfiles.providerValue")}
          <Select
            value={typeof entry.value === "string" ? entry.value : ""}
            onChange={(event) => {
              onError(entry.key, "");
              onChange({ ...entry, value: event.target.value });
            }}
          >
            <option value="">{t("modelProfiles.chooseProvider")}</option>
            {typeof entry.value === "string" &&
              entry.value &&
              !providerNames.includes(entry.value) && (
                <option value={entry.value}>{entry.value}</option>
              )}
            {providerNames.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </Select>
        </label>
      ) : editorType === "boolean" ? (
        <label>
          {t("modelProfiles.value")}
          <Select
            value={String(entry.value)}
            onChange={(event) => {
              onError(entry.key, "");
              onChange({ ...entry, value: event.target.value === "true" });
            }}
          >
            {String(entry.value) === "" && (
              <option value="" disabled>
                {t("modelProfiles.chooseValue")}
              </option>
            )}
            <option value="true">{t("modelProfiles.true")}</option>
            <option value="false">{t("modelProfiles.false")}</option>
          </Select>
        </label>
      ) : (
        <label>
          {t("modelProfiles.value")}
          {typeInfo.lockTypeToReference && reference?.choices?.length ? (
            <>
              <Select
                aria-label={t("modelProfiles.value")}
                aria-describedby={choicesHelpId}
                value={String(entry.value)}
                onChange={(event) => {
                  onError(entry.key, "");
                  onChange({ ...entry, value: event.target.value });
                }}
              >
                {String(entry.value) === "" ? (
                  <option value="" disabled>
                    {t("modelProfiles.chooseValue")}
                  </option>
                ) : !reference.choices.includes(String(entry.value)) ? (
                  <option value={String(entry.value)}>{String(entry.value)}</option>
                ) : null}
                {reference.choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </Select>
              <span id={choicesHelpId} className="field-help">
                {t("modelProfiles.staticChoicesHelp")}
              </span>
            </>
          ) : editorType === "JSON" || editorType === "string list" ? (
            <Textarea value={raw} onChange={(event) => changeRaw(event.target.value)} />
          ) : (
            <Input value={raw} onChange={(event) => changeRaw(event.target.value)} />
          )}
        </label>
      )}
    </div>
  );
}
