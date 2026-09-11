/** One financial year, line by line, grouped the way it was filed. */

import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import { date, money, DASH } from "../lib/format";
import type { LineItem } from "../lib/types";
import { Empty, FactSkeletons, LineSkeletons } from "../components/bits";

interface Group {
  code: string;
  name: string | null;
  items: LineItem[];
}

/** Keeps the filed order, but collects consecutive rows under their group. */
function groupLines(items: LineItem[]): Group[] {
  const groups: Group[] = [];
  for (const item of items) {
    const last = groups.at(-1);
    if (last?.code === item.group_code) last.items.push(item);
    else
      groups.push({
        code: item.group_code,
        name: item.group_name,
        items: [item],
      });
  }
  return groups;
}

export function Statement() {
  const { idno = "", year = "" } = useParams();
  const [params] = useSearchParams();
  const source = params.get("source") ?? undefined;

  const statement = useQuery({
    queryKey: ["statement", idno, year, source],
    queryFn: () => api.statement(idno, Number(year), source),
    retry: (attempt, error) =>
      !(error instanceof ApiError && error.status === 404) && attempt < 2,
  });

  if (statement.isError) {
    return (
      <section className="section">
        <Link className="crumb" to={`/companies/${idno}`} viewTransition>
          ← înapoi la companie
        </Link>
        <Empty
          title="Nu există situație pentru anul ăsta."
          note={(statement.error as Error).message}
        />
      </section>
    );
  }

  const data = statement.data;
  const groups = groupLines(data?.line_items ?? []);
  // Only widen the table when the filing actually used the extra columns.
  const hasPrevious = data?.line_items.some((item) => item.value_previous !== null);

  return (
    <>
      <section className="section">
        <Link className="crumb" to={`/companies/${idno}`} viewTransition>
          ← înapoi la companie
        </Link>

        <h1 className="detail__title">
          {year}
          <span className="detail__qual"> / {data?.entity_name ?? idno}</span>
        </h1>

        <div className="detail__meta">
          <span className="pill" data-tone="idle">
            {data?.source ?? source ?? DASH}
          </span>
          <span className="mono detail__idno">IDNO {idno}</span>
          {data?.is_audited && (
            <span className="pill" data-tone="ok">
              <span className="pill__dot" />
              auditat
            </span>
          )}
          {data?.status && (
            <span className="pill" data-tone="idle">
              {data.status}
            </span>
          )}
        </div>

        {statement.isPending && <FactSkeletons count={4} />}

        {data && (
          <div className="facts">
            <div className="fact">
              <span className="fact__label">perioadă</span>
              <span className="fact__value">
                {date(data.period_from)} – {date(data.period_to)}
              </span>
            </div>
            <div className="fact">
              <span className="fact__label">depusă</span>
              <span className="fact__value">{date(data.declaration_date)}</span>
            </div>
            <div className="fact">
              <span className="fact__label">origine</span>
              <span className="fact__value">{data.origin ?? DASH}</span>
            </div>
            <div className="fact">
              <span className="fact__label">referință</span>
              <span className="fact__value mono" style={{ fontSize: "0.8125rem" }}>
                {data.source_ref ?? DASH}
              </span>
            </div>
          </div>
        )}
      </section>

      <section className="section">
        {statement.isPending && <LineSkeletons count={12} />}

        {data && groups.length === 0 && <Empty title="Situația nu are rânduri." />}

        {groups.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>rând</th>
                  <th>anul curent</th>
                  {hasPrevious && <th>anul precedent</th>}
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <ContentGroup
                    key={group.code}
                    group={group}
                    hasPrevious={Boolean(hasPrevious)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function ContentGroup({
  group,
  hasPrevious,
}: {
  group: Group;
  hasPrevious: boolean;
}) {
  const span = hasPrevious ? 3 : 2;
  return (
    <>
      <tr className="table__group">
        <td colSpan={span}>{group.name ?? group.code}</td>
      </tr>
      {group.items.map((item) => (
        <tr key={`${group.code}-${item.field_code}`}>
          <td>
            {item.label ?? item.field_code}
            <span
              className="mono"
              style={{
                marginInlineStart: "0.6rem",
                fontSize: "0.6875rem",
                color: "var(--text-faint)",
              }}
            >
              {item.field_code}
            </span>
          </td>
          <td data-empty={item.value_current === null}>
            {money(item.value_current)}
          </td>
          {hasPrevious && (
            <td data-empty={item.value_previous === null}>
              {money(item.value_previous)}
            </td>
          )}
        </tr>
      ))}
    </>
  );
}
