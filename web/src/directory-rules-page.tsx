import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "./components/page-header";
import { Button } from "./components/ui/button";
import { Select } from "./components/ui/input";
import { LocalizedErrorMessage } from "./components/ui/localized-error-message";
import { useMediaDirectory } from "./browse";
import { useI18n } from "./i18n";
import { TermMapNavigation } from "./term-map-navigation";
import {
  useBindDirectoryTermMap,
  useDirectoryRules,
  useDirectoryTermMap,
  useRemoveDirectoryTermMap,
  useTermMaps,
  type DirectoryTermMapRule,
} from "./term-maps";

export function DirectoryRulesPage() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const rules = useDirectoryRules();
  const [editing, setEditing] = useState<string | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <PageHeader
        title={t("settings.termMaps")}
        detail={t("termMaps.automaticDetail")}
      />
      <TermMapNavigation />
      <section className="directory-rules" aria-labelledby="directory-rules-title">
        <div className="section-heading">
          <h2 id="directory-rules-title">{t("termMaps.directoryRules")}</h2>
          <Button
            ref={addRef}
            onClick={() => setEditing(params.get("directory") ?? "")}
          >
            {t("termMaps.addRule")}
          </Button>
        </div>
        {rules.isPending && <p role="status">{t("common.loading")}</p>}
        {rules.isFetching && !rules.isPending && (
          <p role="status">{t("common.loading")}</p>
        )}
        {rules.isError && (
          <div className="field-recovery">
            <div className="form-error" role="alert">
              <LocalizedErrorMessage error={rules.error} />
            </div>
            <Button variant="outline" onClick={() => void rules.refetch()}>
              {t("common.tryAgain")}
            </Button>
          </div>
        )}
        {rules.isSuccess && rules.data.rules.length === 0 && (
          <p className="field-help">{t("termMaps.noRules")}</p>
        )}
        <ul className="directory-rule-list">
          {rules.data?.rules.map((rule) => (
            <li key={rule.directory}>
              <div>
                <strong>{rule.directory || t("translate.mediaRoot")}</strong>
                <span>{rule.term_map.name}</span>
              </div>
              <Button
                variant="outline"
                aria-label={t("termMaps.editRuleFor", {
                  name: rule.directory || t("translate.mediaRoot"),
                })}
                onClick={() => setEditing(rule.directory)}
              >
                {t("modelProfiles.edit")}
              </Button>
            </li>
          ))}
        </ul>
        {editing !== null && (
          <DirectoryRuleEditor
            key={editing}
            initialDirectory={editing}
            rules={rules.isSuccess && !rules.isFetching ? rules.data.rules : undefined}
            onClose={() => {
              setEditing(null);
              addRef.current?.focus();
            }}
          />
        )}
      </section>
    </>
  );
}

function DirectoryRuleEditor({
  initialDirectory,
  rules,
  onClose,
}: {
  initialDirectory: string;
  rules?: DirectoryTermMapRule[];
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [directory, setDirectory] = useState(initialDirectory);
  const [selection, setSelection] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const browser = useMediaDirectory(directory);
  const state = useDirectoryTermMap(directory);
  const maps = useTermMaps();
  const save = useBindDirectoryTermMap();
  const remove = useRemoveDirectoryTermMap();
  const selectRef = useRef<HTMLSelectElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
    headingRef.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  const selectedId = selection ?? state.data?.local?.id ?? "";
  const validSelection =
    maps.data?.term_maps.some((map) => map.id === selectedId) ?? false;
  const busy = save.isPending || remove.isPending;
  const canSave =
    !busy &&
    state.isSuccess &&
    maps.isSuccess &&
    !maps.isFetching &&
    validSelection &&
    selectedId !== state.data.local?.id;
  const canDelete =
    !busy && state.isSuccess && !!state.data.local && rules !== undefined;
  const canonicalDirectory = state.data?.directory ?? directory;
  const fallback = rules
    ?.filter(
      (rule) =>
        rule.directory !== canonicalDirectory &&
        (rule.directory === "" || canonicalDirectory.startsWith(`${rule.directory}/`)),
    )
    .sort((a, b) => b.directory.length - a.directory.length)[0];
  const resultName = (name?: string | null) => name ?? t("translate.noTermMapJob");
  const changeDirectory = (path: string) => {
    setDirectory(path);
    setSelection(null);
    setMessage(null);
    save.reset();
    remove.reset();
  };
  const saveRule = () => {
    if (!canSave) return;
    remove.reset();
    save.mutate(
      { path: directory, termMapId: selectedId },
      {
        onSuccess: () => {
          setMessage(t("termMaps.ruleSaved"));
          selectRef.current?.focus();
        },
      },
    );
  };
  const deleteRule = () => {
    if (!canDelete) return;
    save.reset();
    remove.mutate(directory, {
      onSuccess: (next) => {
        setSelection(null);
        setMessage(
          t("termMaps.ruleRemoved", { name: resultName(next.effective?.name) }),
        );
        selectRef.current?.focus();
      },
    });
  };
  return (
    <section className="directory-rule-editor" aria-labelledby="rule-editor-title">
      <div className="section-heading">
        <h3 id="rule-editor-title" ref={headingRef} tabIndex={-1}>
          {t("termMaps.editRule")}
        </h3>
        <Button variant="outline" disabled={busy} onClick={onClose}>
          {t("common.close")}
        </Button>
      </div>
      <p className="field-label">
        {t("translate.currentDirectory", {
          name: directory || t("translate.mediaRoot"),
        })}
      </p>
      <nav className="directory-picker" aria-label={t("termMaps.chooseDirectory")}>
        <Button
          variant="outline"
          disabled={busy || !directory}
          onClick={() => changeDirectory("")}
        >
          {t("translate.mediaRoot")}
        </Button>
        {directory && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              changeDirectory(
                directory.slice(0, Math.max(0, directory.lastIndexOf("/"))),
              )
            }
          >
            {t("termMaps.parentDirectory")}
          </Button>
        )}
        {browser.data?.entries
          .filter((entry) => entry.kind === "directory")
          .map((entry) => (
            <Button
              variant="outline"
              key={entry.path}
              disabled={busy}
              onClick={() => changeDirectory(entry.path)}
            >
              {entry.name}
            </Button>
          ))}
        {browser.isPending && <span role="status">{t("common.loading")}</span>}
        {browser.isError && (
          <div className="field-recovery">
            <div className="form-error" role="alert">
              <LocalizedErrorMessage error={browser.error} />
            </div>
            <Button variant="outline" onClick={() => void browser.refetch()}>
              {t("common.tryAgain")}
            </Button>
          </div>
        )}
      </nav>
      <p id="directory-rule-scope" className="field-help">
        {t("termMaps.ruleScope")}
      </p>
      {state.isPending && <p role="status">{t("common.loading")}</p>}
      {state.isError && (
        <div className="field-recovery">
          <div className="form-error" role="alert">
            <LocalizedErrorMessage error={state.error} />
          </div>
          <Button variant="outline" onClick={() => void state.refetch()}>
            {t("common.tryAgain")}
          </Button>
        </div>
      )}
      {state.isSuccess && (
        <>
          <p id="directory-rule-result">
            {t("termMaps.currentResult", {
              name: resultName(state.data.effective?.name),
            })}
          </p>
          {state.data.effective && !state.data.local && (
            <details>
              <summary>{t("termMaps.viewSource")}</summary>
              <p className="field-help">
                {state.data.source_directory || t("translate.mediaRoot")}
              </p>
            </details>
          )}
        </>
      )}
      <div className="directory-rule-map-field">
        <label htmlFor="directory-rule-map">{t("termMaps.defaultTermMap")}</label>
        <Select
          id="directory-rule-map"
          aria-describedby="directory-rule-scope directory-rule-result"
          ref={selectRef}
          value={selectedId}
          disabled={busy || !state.isSuccess || !maps.isSuccess}
          onChange={(event) => {
            setSelection(event.target.value);
            setMessage(null);
            save.reset();
            remove.reset();
          }}
        >
          <option value="">{t("translate.chooseTermMap")}</option>
          {maps.data?.term_maps.map((map) => (
            <option key={map.id} value={map.id}>
              {map.name}
            </option>
          ))}
        </Select>
      </div>
      {maps.isPending && <p role="status">{t("translate.loadingTermMaps")}</p>}
      {maps.isError && (
        <div className="field-recovery">
          <div className="form-error" role="alert">
            <LocalizedErrorMessage error={maps.error} />
          </div>
          <Button variant="outline" onClick={() => void maps.refetch()}>
            {t("common.tryAgain")}
          </Button>
        </div>
      )}
      {maps.isSuccess && maps.data.term_maps.length === 0 && (
        <p className="field-help">{t("translate.noTermMapsHelp")}</p>
      )}
      <div className="directory-rule-actions">
        <Button disabled={!canSave} onClick={saveRule}>
          {save.isPending ? t("modelProfiles.saving") : t("termMaps.saveRule")}
        </Button>
        {state.data?.local && (
          <Button
            variant="outline"
            disabled={!canDelete}
            onClick={deleteRule}
            aria-describedby="directory-rule-delete-help"
          >
            {remove.isPending ? t("termMaps.deleting") : t("termMaps.deleteRule")}
          </Button>
        )}
      </div>
      {state.data?.local && rules && (
        <p id="directory-rule-delete-help" className="field-help">
          {t("termMaps.removeResult", { name: resultName(fallback?.term_map.name) })}
        </p>
      )}
      {busy && (
        <p role="status">
          {save.isPending ? t("modelProfiles.saving") : t("termMaps.deleting")}
        </p>
      )}
      {(save.error || remove.error) && (
        <div className="field-recovery">
          <div className="form-error" role="alert">
            <LocalizedErrorMessage error={save.error ?? remove.error} />
          </div>
          <Button
            variant="outline"
            disabled={save.error ? !canSave : !canDelete}
            onClick={save.error ? saveRule : deleteRule}
          >
            {t("common.tryAgain")}
          </Button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
