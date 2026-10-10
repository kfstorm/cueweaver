import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useMemo, useState } from "react";

import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { useI18n } from "./i18n";

const PAGE_SIZE = 25;

export function TermMapTable({ content }: { content: Record<string, string> }) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const entries = useMemo(
    () =>
      Object.entries(content).filter(([source, target]) =>
        `${source} ${target}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
      ),
    [content, search],
  );
  const pages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  // Content replacement can remove the current page while keeping this view mounted.
  const currentPage = Math.min(page, pages - 1);

  return (
    <div className="term-map-table">
      <label className="input-chrome search-field">
        <MagnifyingGlassIcon size={17} aria-hidden="true" />
        <span className="sr-only">{t("termMaps.search")}</span>
        <Input
          aria-label={t("termMaps.search")}
          value={search}
          placeholder={t("termMaps.filter")}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(0);
          }}
        />
      </label>
      <div className="term-table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t("termMaps.source")}</th>
              <th scope="col">{t("termMaps.target")}</th>
            </tr>
          </thead>
          <tbody>
            {entries
              .slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE)
              .map(([source, target]) => (
                <tr key={source}>
                  <td>{source}</td>
                  <td>{target}</td>
                </tr>
              ))}
          </tbody>
        </table>
        {entries.length === 0 && (
          <p className="table-empty">{t("termMaps.noMatchingTerms")}</p>
        )}
      </div>
      {pages > 1 && (
        <nav className="table-pagination" aria-label={t("common.pagination")}>
          <Button
            variant="outline"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            {t("common.previousPage")}
          </Button>
          <span role="status">
            {t("common.pageOf", { page: currentPage + 1, pages })}
          </span>
          <Button
            variant="outline"
            disabled={currentPage === pages - 1}
            onClick={() => setPage(currentPage + 1)}
          >
            {t("common.nextPage")}
          </Button>
        </nav>
      )}
    </div>
  );
}
