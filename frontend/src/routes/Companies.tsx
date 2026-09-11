/** The searchable ledger of everything that has been transformed. */

import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { ago, count } from "../lib/format";
import {
  ActivePill,
  Arrow,
  Empty,
  Notice,
  RowSkeletons,
  SearchIcon,
} from "../components/bits";

const PAGE = 25;

export function Companies() {
  const [params, setParams] = useSearchParams();
  const search = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  // The field leads the URL; the URL only catches up once typing settles.
  const [draft, setDraft] = useState(search);

  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => {
      setParams(draft ? { q: draft } : {}, { replace: true });
    }, 280);
    return () => clearTimeout(timer);
  }, [draft, search, setParams]);

  const companies = useQuery({
    queryKey: ["companies", { search, limit: PAGE, offset }],
    queryFn: () => api.companies({ search, limit: PAGE, offset }),
    placeholderData: keepPreviousData,
  });

  const total = companies.data?.total ?? 0;
  const shown = companies.data?.items.length ?? 0;
  const hasMore = offset + shown < total;

  const goTo = (next: number) => {
    const patch: Record<string, string> = {};
    if (search) patch.q = search;
    if (next > 0) patch.offset = String(next);
    setParams(patch);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <>
      <section className="section">
        <div className="page-head">
          <div>
            <h1 className="page-head__title">Companii</h1>
            <p className="page-head__sub">
              {companies.isPending
                ? "Număr registrul…"
                : `${count(total)} în registru. Caută după nume sau IDNO.`}
            </p>
          </div>
        </div>

        <div className="search">
          <SearchIcon />
          <input
            className="search__field"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="moldcell, 1002600012345, chișinău…"
            autoComplete="off"
            spellCheck={false}
          />
          {companies.isFetching && <span className="eyebrow">caut…</span>}
        </div>
      </section>

      <section className="section">
        {companies.isError && (
          <Notice>{(companies.error as Error).message}</Notice>
        )}

        {companies.isPending && <RowSkeletons count={8} />}

        {companies.data?.items.length === 0 && (
          <Empty
            title={search ? "Nimic pe căutarea asta." : "Registrul e gol."}
            note={
              search
                ? "Încearcă doar numele, fără forma juridică."
                : "Pornește un scrape ca să populezi registrul."
            }
          />
        )}

        <div className="reveal rows">
          {companies.data?.items.map((company, index) => (
            <Link
              className="row"
              key={company.idno}
              to={`/companies/${company.idno}`}
              viewTransition
            >
              <span className="row__index">
                {String(offset + index + 1).padStart(3, "0")}
              </span>
              <span>
                <span className="row__name">{company.name ?? company.idno}</span>
                <span className="row__meta">
                  <span>{company.idno}</span>
                  {company.city && <span>{company.city}</span>}
                  {company.legal_form && <span>{company.legal_form}</span>}
                  {company.caem_code && <span>CAEM {company.caem_code}</span>}
                </span>
              </span>
              <span className="row__aside cluster">
                <ActivePill active={company.is_active} />
                <span>{ago(company.transformed_at)}</span>
              </span>
              <Arrow />
            </Link>
          ))}
        </div>

        {(offset > 0 || hasMore) && (
          <div
            className="cluster"
            style={{ marginBlockStart: "2rem", justifyContent: "space-between" }}
          >
            <button
              className="btn"
              type="button"
              disabled={offset === 0}
              onClick={() => goTo(Math.max(0, offset - PAGE))}
            >
              ← înapoi
            </button>
            <span className="eyebrow">
              {count(offset + 1)}–{count(offset + shown)} din {count(total)}
            </span>
            <button
              className="btn"
              type="button"
              disabled={!hasMore}
              onClick={() => goTo(offset + PAGE)}
            >
              înainte →
            </button>
          </div>
        )}
      </section>
    </>
  );
}
