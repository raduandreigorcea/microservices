/** One company in full: identity, people, years on file, raw captures. */

import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import { Network } from "../components/Network";
import { describeGraph, touchedCompanies } from "../lib/graph";
import { ago, count, date, dateTime, DASH } from "../lib/format";
import {
  ActivePill,
  Empty,
  Fact,
  FactSkeletons,
  Notice,
  Skeleton,
} from "../components/bits";

export function Company() {
  const { idno = "" } = useParams();

  const company = useQuery({
    queryKey: ["company", idno],
    queryFn: () => api.company(idno),
    retry: (attempt, error) =>
      !(error instanceof ApiError && error.status === 404) && attempt < 2,
  });

  const graph = useQuery({
    queryKey: ["ego-graph", idno],
    queryFn: () => api.companyGraph(idno),
    enabled: company.isSuccess,
  });

  const captures = useQuery({
    queryKey: ["source-data", idno],
    queryFn: () => api.sourceData(idno, 12),
    enabled: company.isSuccess,
  });

  if (company.isError) {
    const notFound =
      company.error instanceof ApiError && company.error.status === 404;
    return (
      <section className="section">
        <Link className="crumb" to="/companies" viewTransition>
          ← companii
        </Link>
        <Empty
          title={notFound ? "Compania asta nu a fost scrapată încă." : "Nu am putut încărca."}
          note={notFound ? idno : (company.error as Error).message}
        />
      </section>
    );
  }

  const data = company.data;
  const touches = graph.data ? touchedCompanies(graph.data, idno) : [];
  const years = [...(data?.statements ?? [])].sort((a, b) => b.year - a.year);

  return (
    <>
      <section className="section">
        <Link className="crumb" to="/companies" viewTransition>
          ← companii
        </Link>

        <h1 className="detail__title">
          {company.isPending ? (
            <Skeleton w="60%" h="1em" />
          ) : (
            (data?.name ?? idno)
          )}
        </h1>

        <div className="detail__meta">
          <span className="mono detail__idno">IDNO {idno}</span>
          {data && <ActivePill active={data.is_active} />}
          {data?.in_liquidation && (
            <span className="pill" data-tone="bad">
              <span className="pill__dot" />
              în lichidare
            </span>
          )}
          {data?.sources?.map((source) => (
            <span className="pill" key={String(source)} data-tone="idle">
              {String(source)}
            </span>
          ))}
        </div>

        {company.isPending && <FactSkeletons count={10} />}

        {data && (
          <div className="facts">
            <Fact label="formă juridică">{data.legal_form ?? DASH}</Fact>
            <Fact label="înregistrată">{date(data.registered_at)}</Fact>
            <Fact label="localitate">{data.cuatm_name ?? data.city ?? DASH}</Fact>
            <Fact label="adresă">{data.address ?? data.street ?? DASH}</Fact>
            <Fact label="CAEM">
              {data.caem_code ? `${data.caem_code} · ${data.caem_name ?? ""}` : DASH}
            </Fact>
            <Fact label="angajați">{count(data.employees)}</Fact>
            <Fact label="proprietate">{data.ownership_name ?? DASH}</Fact>
            <Fact label="contact">
              {data.email ? (
                <a href={`mailto:${data.email}`}>{data.email}</a>
              ) : (
                (data.phone ?? DASH)
              )}
            </Fact>
            <Fact label="web">
              {data.website ? (
                <a href={data.website} target="_blank" rel="noreferrer noopener">
                  {data.website.replace(/^https?:\/\//, "")}
                </a>
              ) : (
                DASH
              )}
            </Fact>
            <Fact label="transformată">{ago(data.transformed_at)}</Fact>
          </div>
        )}
      </section>

      <section className="section">
        <div className="head">
          <h2 className="head__title">Situații financiare</h2>
          <span className="eyebrow">{count(years.length)} ani pe dosar</span>
        </div>

        {years.length === 0 ? (
          <Empty title="Niciun an depus." />
        ) : (
          <div className="years">
            {years.map((statement) => (
              <Link
                className="year"
                key={`${statement.year}-${statement.source}`}
                to={`/companies/${idno}/statements/${statement.year}?source=${statement.source}`}
                viewTransition
              >
                <span className="year__n">{statement.year}</span>
                <span className="year__src">{statement.source}</span>
                {statement.is_audited && <span className="year__src">auditat</span>}
              </Link>
            ))}
          </div>
        )}
      </section>

      {data && data.people.length > 0 && (
        <section className="section">
          <div className="head">
            <h2 className="head__title">Persoane</h2>
            <span className="eyebrow">{count(data.people.length)} înregistrate</span>
          </div>
          <div className="reveal rows">
            {data.people.map((person, index) => (
              <div className="row" key={`${person.full_name}-${index}`}>
                <span className="row__index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>
                  <span className="row__name">
                    {person.full_name}
                  </span>
                  <span className="row__meta">
                    <span>{person.role}</span>
                    <span>{person.party_type}</span>
                  </span>
                </span>
                <span className="row__aside">
                  {person.share_percent ? `${person.share_percent}%` : DASH}
                </span>
                <span />
              </div>
            ))}
          </div>
        </section>
      )}

      {graph.data && graph.data.links.length > 0 && (
        <section className="section">
          <div className="head">
            <h2 className="head__title">Rețeaua firmei</h2>
            <span className="eyebrow">{describeGraph(graph.data, idno)}</span>
          </div>

          {touches.length > 0 ? (
            <ul className="touches">
              {touches.map(({ company: other, via }) => (
                <li className="touch" key={other.id}>
                  <span className="touch__dot" data-kind={other.kind} />
                  <span className="touch__name">
                    {other.kind === "company" && other.idno ? (
                      <Link to={`/companies/${other.idno}`} viewTransition>
                        {other.label}
                      </Link>
                    ) : (
                      other.label
                    )}
                  </span>
                  <span className="touch__via">
                    {via.length === 0
                      ? "legătură directă"
                      : `prin ${via.map((node) => node.label).join(", ")}`}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="field__hint" style={{ marginBlockStart: "1rem" }}>
              Nicio parte a acestei firme nu apare la altă firmă din registru.
            </p>
          )}

          <Network
            nodes={graph.data.nodes}
            links={graph.data.links}
            focus={`c:${idno}`}
            width={1000}
            height={520}
          />
        </section>
      )}

      {captures.data && captures.data.length > 0 && (
        <section className="section">
          <div className="head">
            <h2 className="head__title">Capturi brute</h2>
            <span className="eyebrow">stratul raw, fără corp</span>
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>resursă</th>
                  <th>sursă</th>
                  <th>status</th>
                  <th>văzută ultima dată</th>
                </tr>
              </thead>
              <tbody>
                {captures.data.map((capture) => (
                  <tr key={capture.id}>
                    <td>{capture.resource}</td>
                    <td>{capture.source}</td>
                    <td
                      style={{
                        color:
                          capture.http_status >= 400
                            ? "var(--rose)"
                            : "var(--accent)",
                      }}
                    >
                      {capture.http_status}
                    </td>
                    <td>{dateTime(capture.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {captures.isError && (
        <section className="section">
          <Notice>{(captures.error as Error).message}</Notice>
        </section>
      )}
    </>
  );
}
