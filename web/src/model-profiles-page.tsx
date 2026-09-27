import { useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "./components/page-header";
import { Button } from "./components/ui/button";
import { Input, Select, Textarea } from "./components/ui/input";
import { useI18n } from "./i18n";
import {
  useDeleteModelProfile,
  useModelProfiles,
  useSaveModelProfile,
  type ModelProfile,
  type ProfileInput,
  type ProfileSetting,
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
  onSave,
  pending,
  error,
}: {
  current?: ModelProfile;
  initial: ProfileInput;
  profiles: ModelProfile[];
  onSave: (input: ProfileInput) => void;
  pending: boolean;
  error?: string;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(initial);
  const [newKey, setNewKey] = useState("");
  const [parseErrors, setParseErrors] = useState<Map<string, string>>(() => new Map());
  const setParseError = (key: string, message: string) =>
    setParseErrors((previous) => {
      const next = new Map(previous);
      if (message) next.set(key, message);
      else next.delete(key);
      return next;
    });
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
  const change = (entry: ProfileSetting) => {
    if (entry.kind === "unset") setParseError(entry.key, "");
    setDraft((previous) => ({
      ...previous,
      settings: [...previous.settings.filter((item) => item.key !== entry.key), entry],
    }));
  };
  const remove = (key: string) => {
    setParseError(key, "");
    setDraft((previous) => ({
      ...previous,
      settings: previous.settings.filter((item) => item.key !== key),
    }));
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSave(draft);
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
                  {entry
                    ? valueType(entry)
                    : parentEntry
                      ? valueType({ key, kind: "literal", value: parentEntry.value })
                      : ""}
                </span>
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
                  entry={entry}
                  onChange={change}
                  onError={(message) => setParseError(key, message)}
                />
              )}
            </div>
          );
        })}
        <div className="profile-add">
          <label>
            {t("modelProfiles.newKey")}
            <Input value={newKey} onChange={(event) => setNewKey(event.target.value)} />
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={!newKey.trim() || keys.includes(newKey)}
            onClick={() => {
              change({ key: newKey, kind: "literal", value: "" });
              setNewKey("");
            }}
          >
            {t("modelProfiles.addSetting")}
          </Button>
        </div>
        {[...parseErrors].map(([key, message]) => (
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
          <Button type="submit" disabled={pending || parseErrors.size > 0}>
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
  onChange,
  onError,
}: {
  entry: ProfileSetting;
  onChange: (entry: ProfileSetting) => void;
  onError: (message: string) => void;
}) {
  const { t } = useI18n();
  const type = valueType(entry);
  const [raw, setRaw] = useState(inputValue(entry.value));
  const [editorType, setEditorType] = useState(type);
  const changeType = (next: string) => {
    setEditorType(next);
    onError("");
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
      onError("");
      onChange({ ...entry, value });
    } catch {
      onError(t("modelProfiles.invalidValue", { type: editorType, key: entry.key }));
    }
  };
  return (
    <div className="profile-editor">
      <label>
        {t("modelProfiles.valueType")}
        <Select value={editorType} onChange={(event) => changeType(event.target.value)}>
          {["string", "integer", "number", "boolean", "string list", "JSON"].map(
            (item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ),
          )}
        </Select>
      </label>
      {editorType === "boolean" ? (
        <label>
          {t("modelProfiles.value")}
          <Select
            value={String(entry.value)}
            onChange={(event) =>
              onChange({ ...entry, value: event.target.value === "true" })
            }
          >
            <option value="true">{t("modelProfiles.true")}</option>
            <option value="false">{t("modelProfiles.false")}</option>
          </Select>
        </label>
      ) : (
        <label>
          {t("modelProfiles.value")}
          {editorType === "JSON" || editorType === "string list" ? (
            <Textarea value={raw} onChange={(event) => changeRaw(event.target.value)} />
          ) : (
            <Input value={raw} onChange={(event) => changeRaw(event.target.value)} />
          )}
        </label>
      )}
    </div>
  );
}
