/** Every scrape run, newest first, polling while any of them is alive. */

import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { ago, count, duration, short } from "../lib/format";
import { FINAL_STATUSES, type JobStatus } from "../lib/types";
import {
  Arrow,
  Empty,
  JobPill,
  Meter,
  Notice,
  RowSkeletons,
} from "../components/bits";

const FILTERS: { value: JobStatus | ""; label: string }[] = [
  { value: "", label: "toate" },
  { value: "running", label: "rulează" },
  { value: "succeeded", label: "reușite" },
  { value: "failed", label: "eșuate" },
  { value: "cancelled", label: "anulate" },
];

export const isLive = (status: JobStatus) => !FINAL_STATUSES.includes(status);

export function Jobs() {
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") ?? "") as JobStatus | "";

  const jobs = useQuery({
    queryKey: ["jobs", { limit: 50, status }],
    queryFn: () => api.jobs({ limit: 50, status: status || undefined }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.some((job) => isLive(job.status)) ? 4000 : false,
  });

  return (
    <>
      <section className="section">
        <div className="page-head">
          <div>
            <h1 className="page-head__title">Joburi</h1>
            <p className="page-head__sub">
              {jobs.data?.some((job) => isLive(job.status))
                ? "Ceva rulează. Se actualizează la 4 secunde."
                : "Nimic nu rulează acum."}
            </p>
          </div>
          <Link className="btn btn--accent" to="/scrape" viewTransition>
            job nou
          </Link>
        </div>

        <div className="filters">
          {FILTERS.map((filter) => (
            <button
              key={filter.value || "all"}
              type="button"
              className={`nav__link${status === filter.value ? " is-active" : ""}`}
              onClick={() =>
                setParams(filter.value ? { status: filter.value } : {})
              }
            >
              {filter.label}
            </button>
          ))}
        </div>
      </section>

      <section className="section">
        {jobs.isError && <Notice>{(jobs.error as Error).message}</Notice>}

        {jobs.isPending && <RowSkeletons count={6} />}

        {jobs.data?.length === 0 && (
          <Empty
            title="Niciun job pe filtrul ăsta."
            note="Schimbă filtrul sau pornește unul nou."
          />
        )}

        <div className="reveal rows">
          {jobs.data?.map((job, index) => {
            const total = job.companies_total ?? 0;
            const progress = total > 0 ? job.companies_done / total : 0;
            return (
              <Link
                className="row"
                key={job.id}
                to={`/jobs/${job.id}`}
                viewTransition
              >
                <span className="row__index">
                  {String(index + 1).padStart(3, "0")}
                </span>
                <span>
                  <span className="row__name">
                    {job.mode === "sweep" ? "Sweep registru" : "Listă IDNO"}
                  </span>
                  <span className="row__meta">
                    <span>{ago(job.created_at)}</span>
                    <span>
                      {count(job.companies_done)}
                      {total > 0 ? ` / ${count(total)}` : ""} companii
                    </span>
                    <span>{short(job.rows_loaded)} rânduri</span>
                    {job.requests_failed > 0 && (
                      <span style={{ color: "var(--rose)" }}>
                        {count(job.requests_failed)} eșecuri
                      </span>
                    )}
                  </span>
                </span>
                <span className="row__aside cluster">
                  {total > 0 && (
                    <Meter
                      value={progress}
                      tone={job.status === "failed" ? "bad" : "ok"}
                      label={`Progres job ${job.mode}`}
                    />
                  )}
                  <span>{duration(job.started_at, job.finished_at)}</span>
                  <JobPill status={job.status} />
                </span>
                <Arrow />
              </Link>
            );
          })}
        </div>
      </section>
    </>
  );
}
