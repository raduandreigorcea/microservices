/** A single run, watched live, with the cancel button. */

import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import { count, dateTime, duration, short, DASH } from "../lib/format";
import { FINAL_STATUSES } from "../lib/types";
import {
  Empty,
  Fact,
  FactSkeletons,
  JobPill,
  Meter,
  Notice,
  Skeleton,
  Spinner,
} from "../components/bits";

export function Job() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();

  const job = useQuery({
    queryKey: ["job", id],
    queryFn: () => api.job(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !FINAL_STATUSES.includes(status) ? 2000 : false;
    },
    retry: (attempt, error) =>
      !(error instanceof ApiError && error.status === 404) && attempt < 2,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelJob(id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["job", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  if (job.isError) {
    return (
      <section className="section">
        <Link className="crumb" to="/jobs" viewTransition>
          ← joburi
        </Link>
        <Empty title="Jobul ăsta nu există." note={(job.error as Error).message} />
      </section>
    );
  }

  const data = job.data;
  const total = data?.companies_total ?? 0;
  const progress = total > 0 ? (data?.companies_done ?? 0) / total : 0;
  const live = data ? !FINAL_STATUSES.includes(data.status) : false;
  const idnos = Array.isArray(data?.params.idnos)
    ? (data.params.idnos as string[])
    : [];

  return (
    <>
      <section className="section">
        <Link className="crumb" to="/jobs" viewTransition>
          ← joburi
        </Link>

        <h1 className="detail__title">
          {job.isPending ? (
            <Skeleton w="45%" h="1em" />
          ) : data?.mode === "sweep" ? (
            <>
              Sweep <em className="em">registru</em>
            </>
          ) : (
            <>
              Listă <em className="em">IDNO</em>
            </>
          )}
        </h1>

        <div className="detail__meta">
          {data && <JobPill status={data.status} />}
          <span className="mono detail__idno">{id}</span>
          {live && (
            <button
              className={`btn btn--danger push${cancel.isPending ? " is-busy" : ""}`}
              type="button"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {cancel.isPending && <Spinner />}
              {cancel.isPending ? "se oprește…" : "anulează"}
            </button>
          )}
        </div>

        {cancel.isError && (
          <div style={{ marginBlockStart: "1.25rem" }}>
            <Notice>{(cancel.error as Error).message}</Notice>
          </div>
        )}

        {data?.last_error && (
          <div style={{ marginBlockStart: "1.25rem" }}>
            <Notice>{data.last_error}</Notice>
          </div>
        )}

        {total > 0 && (
          <div style={{ marginBlockStart: "2rem" }}>
            <div className="cluster" style={{ justifyContent: "space-between" }}>
              <span className="eyebrow">progres</span>
              <span className="mono" style={{ fontSize: "0.8125rem" }}>
                {count(data?.companies_done)} / {count(total)}
              </span>
            </div>
            <div style={{ marginBlockStart: "0.6rem" }}>
              <Meter
                value={progress}
                tone={data?.status === "failed" ? "bad" : live ? "running" : "ok"}
                label="Progresul jobului"
              />
            </div>
          </div>
        )}

        {job.isPending && <FactSkeletons count={8} />}

        {data && (
          <div className="facts">
            <Fact label="companii procesate">{count(data.companies_done)}</Fact>
            <Fact label="rânduri scrise">{short(data.rows_loaded)}</Fact>
            <Fact label="cereri reușite">{short(data.requests_ok)}</Fact>
            <Fact label="cereri eșuate">{short(data.requests_failed)}</Fact>
            <Fact label="creat">{dateTime(data.created_at)}</Fact>
            <Fact label="pornit">{dateTime(data.started_at)}</Fact>
            <Fact label="terminat">{dateTime(data.finished_at)}</Fact>
            <Fact label="durată">{duration(data.started_at, data.finished_at)}</Fact>
          </div>
        )}
      </section>

      {idnos.length > 0 && (
        <section className="section">
          <div className="head">
            <h2 className="head__title">IDNO-uri cerute</h2>
            <span className="eyebrow">{count(idnos.length)} în lot</span>
          </div>
          <div
            className="cluster"
            style={{ marginBlockStart: "1.25rem", "--cluster-gap": "0.5rem" } as React.CSSProperties}
          >
            {idnos.map((idno) => (
              <Link className="pill" key={idno} to={`/companies/${idno}`} viewTransition>
                {idno}
              </Link>
            ))}
          </div>
        </section>
      )}

      {data && Object.keys(data.cursor).length > 0 && (
        <section className="section">
          <div className="head">
            <h2 className="head__title">Cursor</h2>
            <span className="eyebrow">de unde reia</span>
          </div>
          <div className="facts">
            {Object.entries(data.cursor).map(([key, value]) => (
              <Fact key={key} label={key}>
                <span className="mono">{value === null ? DASH : String(value)}</span>
              </Fact>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
