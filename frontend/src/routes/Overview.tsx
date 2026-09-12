/** The panel: how much is indexed, what is running, what landed last. */

import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, count, short } from "../lib/format";
import { FINAL_STATUSES, type JobStatus } from "../lib/types";
import { Arrow, Empty, JobPill, Meter, RowSkeletons } from "../components/bits";

/** A job that has not reached a final status is worth watching closely. */
const isLive = (status: JobStatus) => !FINAL_STATUSES.includes(status);

export function Overview() {
  const { user } = useAuth();

  const companies = useQuery({
    queryKey: ["companies", { limit: 6, offset: 0, search: "" }],
    queryFn: () => api.companies({ limit: 6, offset: 0 }),
  });

  const jobs = useQuery({
    queryKey: ["jobs", { limit: 20 }],
    queryFn: () => api.jobs({ limit: 20 }),
    // Poll only while something is actually moving.
    refetchInterval: (query) =>
      query.state.data?.some((job) => isLive(job.status)) ? 4000 : false,
  });

  const running = jobs.data?.filter((job) => isLive(job.status)) ?? [];
  const rowsLoaded =
    jobs.data?.reduce((total, job) => total + job.rows_loaded, 0) ?? null;
  const failedRequests =
    jobs.data?.reduce((total, job) => total + job.requests_failed, 0) ?? null;

  const firstName = user?.full_name?.split(" ")[0];
  const greeting = firstName ? `Salut, ${firstName}.` : "Sesiune activă.";
  const activity = jobs.isPending
    ? "Verific joburile…"
    : running.length > 0
      ? `${count(running.length)} în lucru acum.`
      : "Nimic nu rulează acum.";

  return (
    <>
      <section className="section">
        <div className="page-head">
          <div>
            <h1 className="page-head__title">Panou</h1>
            <p className="page-head__sub">
              {greeting} {activity}
            </p>
          </div>
          <Link className="btn btn--accent" to="/scrape" viewTransition>
            job nou
          </Link>
        </div>

        <div className="stats">
          <div className="stat">
            <span className="eyebrow">companii indexate</span>
            <span className="stat__value" data-tone="accent">
              {companies.isPending ? "···" : count(companies.data?.total)}
            </span>
            <span className="stat__note">total în baza transformată</span>
          </div>

          <div className="stat">
            <span className="eyebrow">joburi active</span>
            <span
              className="stat__value"
              data-tone={running.length > 0 ? "amber" : undefined}
            >
              {jobs.isPending ? "···" : count(running.length)}
            </span>
            <span className="stat__note">
              din ultimele {count(jobs.data?.length ?? 0)} rulări
            </span>
          </div>

          <div className="stat">
            <span className="eyebrow">rânduri scrise</span>
            <span className="stat__value">
              {jobs.isPending ? "···" : short(rowsLoaded)}
            </span>
            <span className="stat__note">cumulat pe joburile recente</span>
          </div>

          <div className="stat">
            <span className="eyebrow">cereri eșuate</span>
            <span
              className="stat__value"
              data-tone={failedRequests ? "rose" : undefined}
            >
              {jobs.isPending ? "···" : short(failedRequests)}
            </span>
            <span className="stat__note">sursele dau 403 dacă insiști</span>
          </div>
        </div>
      </section>

      <div className="panels">
        <section className="panel">
          <div className="head">
            <h2 className="head__title">Joburi recente</h2>
            <Link className="crumb" to="/jobs" viewTransition>
              toate →
            </Link>
          </div>

          {jobs.isPending && <RowSkeletons count={4} />}

          {jobs.data?.length === 0 && (
            <Empty
              title="Încă niciun job."
              note="Pornește primul scrape din Scrape."
            />
          )}

          <div className="reveal rows">
            {jobs.data?.slice(0, 5).map((job, index) => {
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
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <span className="row__name">
                      {job.mode === "sweep" ? "Registru sweep" : "Listă IDNO"}
                    </span>
                    <span className="row__meta">
                      <span>{ago(job.created_at)}</span>
                      <span>{count(job.companies_done)} companii</span>
                      <span>{short(job.rows_loaded)} rânduri</span>
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
                    <JobPill status={job.status} />
                  </span>
                  <Arrow />
                </Link>
              );
            })}
          </div>
        </section>

        <section className="panel">
          <div className="head">
            {/* Alphabetical, not recent: /companies orders by name. */}
            <h2 className="head__title">Companii din registru</h2>
            <Link className="crumb" to="/companies" viewTransition>
              caută →
            </Link>
          </div>

          {companies.isPending && <RowSkeletons count={4} />}

          {companies.data?.items.length === 0 && (
            <Empty
              title="Registrul e gol."
              note="Nimic nu a fost încă transformat."
            />
          )}

          <div className="reveal rows">
            {companies.data?.items.slice(0, 5).map((company, index) => (
              <Link
                className="row"
                key={company.idno}
                to={`/companies/${company.idno}`}
                viewTransition
              >
                <span className="row__index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>
                  <span className="row__name">
                    {company.name ?? company.idno}
                  </span>
                  <span className="row__meta">
                    <span>{company.idno}</span>
                    {company.city && <span>{company.city}</span>}
                  </span>
                </span>
                <span className="row__aside">{company.legal_form ?? ""}</span>
                <Arrow />
              </Link>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
