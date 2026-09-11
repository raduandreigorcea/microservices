/** Starting a run: either a list of IDNOs, or a walk through the register. */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";
import { count, isIdno, parseIdnos } from "../lib/format";
import type { Job } from "../lib/types";
import { Notice, Spinner } from "../components/bits";

type Mode = "idno" | "sweep";

export function NewScrape() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<Mode>("idno");
  const [raw, setRaw] = useState("");
  const [includeDepozitar, setIncludeDepozitar] = useState(true);
  const [startPage, setStartPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [maxPages, setMaxPages] = useState(10);

  const parsed = useMemo(() => {
    const all = parseIdnos(raw);
    return { valid: all.filter(isIdno), invalid: all.filter((v) => !isIdno(v)) };
  }, [raw]);

  const start = useMutation({
    mutationFn: (): Promise<Job> =>
      mode === "idno"
        ? api.scrapeByIdno(parsed.valid, includeDepozitar)
        : api.sweep({
            start_page: startPage,
            page_size: pageSize,
            max_pages: maxPages,
            include_depozitar: includeDepozitar,
          }),
    onSuccess: (job) => {
      queryClient.setQueryData(["job", job.id], job);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void navigate(`/jobs/${job.id}`);
    },
  });

  const canStart = mode === "sweep" || parsed.valid.length > 0;

  return (
    <>
      <section className="section">
        <div className="page-head">
          <div>
            <h1 className="page-head__title">Scrape nou</h1>
            <p className="page-head__sub">
              Jobul pornește imediat. Sursele sunt interogate la intervalul
              minim configurat, altfel răspund cu 403.
            </p>
          </div>
        </div>

        <div className="filters">
          <button
            type="button"
            className={`nav__link${mode === "idno" ? " is-active" : ""}`}
            onClick={() => setMode("idno")}
          >
            listă IDNO
          </button>
          <button
            type="button"
            className={`nav__link${mode === "sweep" ? " is-active" : ""}`}
            onClick={() => setMode("sweep")}
          >
            sweep registru
          </button>
        </div>
      </section>

      <section className="section">
        <form
          className="form"
          onSubmit={(event) => {
            event.preventDefault();
            if (canStart) start.mutate();
          }}
        >
          {mode === "idno" ? (
            <>
              <div className="field">
                <label className="field__label" htmlFor="idnos">
                  IDNO-uri
                </label>
                <textarea
                  id="idnos"
                  className="field__area"
                  value={raw}
                  onChange={(event) => setRaw(event.target.value)}
                  placeholder={"1002600012345\n1003600045678"}
                  spellCheck={false}
                />
                <p className="field__hint">
                  Lipește oricâte, separate prin virgulă, spațiu sau linie nouă.
                  Un IDNO are 13 cifre. Maximum 500 per job.
                </p>
              </div>

              <div className="cluster">
                <span className="pill" data-tone={parsed.valid.length ? "ok" : "idle"}>
                  <span className="pill__dot" />
                  {count(parsed.valid.length)} valide
                </span>
                {parsed.invalid.length > 0 && (
                  <span className="pill" data-tone="bad">
                    <span className="pill__dot" />
                    {count(parsed.invalid.length)} ignorate
                  </span>
                )}
              </div>
            </>
          ) : (
            <div
              className="stack"
              style={{ "--stack-gap": "1.25rem" } as React.CSSProperties}
            >
              <div className="field">
                <label className="field__label" htmlFor="start-page">
                  pagina de start
                </label>
                <input
                  id="start-page"
                  className="field__input"
                  type="number"
                  min={0}
                  value={startPage}
                  onChange={(event) => setStartPage(Number(event.target.value))}
                />
                <p className="field__hint">
                  Sweep parcurge registrul pagină cu pagină, fără să-i dai
                  IDNO-uri. Zero pornește de la început, iar jobul își ține
                  propriul cursor și reia de acolo.
                </p>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="page-size">
                  companii per pagină
                </label>
                <input
                  id="page-size"
                  className="field__input"
                  type="number"
                  min={1}
                  max={200}
                  value={pageSize}
                  onChange={(event) => setPageSize(Number(event.target.value))}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="max-pages">
                  pagini maxime
                </label>
                <input
                  id="max-pages"
                  className="field__input"
                  type="number"
                  min={1}
                  max={100000}
                  value={maxPages}
                  onChange={(event) => setMaxPages(Number(event.target.value))}
                />
                <p className="field__hint">
                  Cel mult {count(pageSize * maxPages)} companii în rularea asta.
                </p>
              </div>
            </div>
          )}

          <label className="switch">
            <input
              type="checkbox"
              checked={includeDepozitar}
              onChange={(event) => setIncludeDepozitar(event.target.checked)}
            />
            <span>
              Ia și declarațiile de la depozitar
              <span className="field__hint" style={{ display: "block" }}>
                Mai lent, dar aduce situațiile financiare depuse oficial.
              </span>
            </span>
          </label>

          {start.isError && <Notice>{(start.error as Error).message}</Notice>}

          <div className="cluster">
            <button
              className={`btn btn--accent btn--big${start.isPending ? " is-busy" : ""}`}
              type="submit"
              disabled={!canStart || start.isPending}
            >
              {start.isPending && <Spinner />}
              {start.isPending ? "pornesc…" : "pornește jobul"}
            </button>
            {mode === "idno" && parsed.valid.length === 0 && (
              <span className="eyebrow">adaugă cel puțin un IDNO valid</span>
            )}
          </div>
        </form>
      </section>
    </>
  );
}
