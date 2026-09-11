/** The small, repeated pieces. Anything used on more than one screen. */

import type { ReactNode } from "react";

import type { JobStatus } from "../lib/types";

type Tone = "ok" | "bad" | "running" | "idle";

const JOB_TONE: Record<JobStatus, Tone> = {
  pending: "idle",
  running: "running",
  succeeded: "ok",
  failed: "bad",
  cancelled: "idle",
};

const JOB_LABEL: Record<JobStatus, string> = {
  pending: "în așteptare",
  running: "rulează",
  succeeded: "reușit",
  failed: "eșuat",
  cancelled: "anulat",
};

export function JobPill({ status }: { status: JobStatus }) {
  return (
    <span className="pill" data-tone={JOB_TONE[status]}>
      <span className="pill__dot" />
      {JOB_LABEL[status] ?? status}
    </span>
  );
}

export function ActivePill({ active }: { active: boolean | null }) {
  if (active === null) return null;
  return (
    <span className="pill" data-tone={active ? "ok" : "idle"}>
      <span className="pill__dot" />
      {active ? "activ" : "inactiv"}
    </span>
  );
}

export function Arrow() {
  return (
    <svg
      className="row__go"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M5 12h13M13 6l6 6-6 6" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg
      className="search__icon"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m16.5 16.5 4 4" />
    </svg>
  );
}

export function Notice({
  tone = "bad",
  children,
}: {
  tone?: "bad" | "ok" | "neutral";
  children: ReactNode;
}) {
  return (
    <p className="notice" data-tone={tone}>
      {children}
    </p>
  );
}

export function Empty({ title, note }: { title: string; note?: string }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {note && <p style={{ marginBlockStart: "0.75rem" }}>{note}</p>}
    </div>
  );
}

/** The in-button working indicator. */
export function Spinner() {
  return <span className="spinner" />;
}

/** One shimmering bar. Dimensions are the only thing callers vary. */
export function Skeleton({
  w = "100%",
  h = "1rem",
}: {
  w?: string;
  h?: string;
}) {
  return <span className="skeleton" style={{ inlineSize: w, blockSize: h }} />;
}

/** Wraps a set of placeholders. */
function Loading({
  className,
  children,
}: {
  className: string;
  children: ReactNode;
}) {
  return (
    <div className={`${className} is-loading`}>
      {children}
    </div>
  );
}

/** Varies the bar widths so a stack of rows does not read as a barcode. */
const jitter = (index: number, base: number, spread: number) =>
  `${base + ((index * 37) % spread)}%`;

/**
 * Placeholder rows. Fills all four columns of the row grid so the list does
 * not visibly reflow when the real data replaces it.
 */
export function RowSkeletons({ count = 6 }: { count?: number }) {
  return (
    <Loading className="rows">
      {Array.from({ length: count }, (_, index) => (
        <div className="row" key={index}>
          <span className="row__index">
            <Skeleton w="2ch" h="0.7rem" />
          </span>
          <span>
            <Skeleton w={jitter(index, 38, 34)} h="1.25rem" />
            <span className="row__meta">
              <Skeleton w={jitter(index, 22, 16)} h="0.7rem" />
            </span>
          </span>
          <span className="row__aside">
            <Skeleton w="4.5rem" h="0.9rem" />
          </span>
          <Skeleton w="1.1rem" h="1.1rem" />
        </div>
      ))}
    </Loading>
  );
}

/** Placeholder lines for a statement, shaped like label plus two figures. */
export function LineSkeletons({ count = 10 }: { count?: number }) {
  return (
    <Loading className="lines">
      {Array.from({ length: count }, (_, index) => (
        <div className="lines__row" key={index}>
          <Skeleton w={jitter(index, 40, 45)} h="0.9rem" />
          <Skeleton w="5rem" h="0.9rem" />
          <Skeleton w="5rem" h="0.9rem" />
        </div>
      ))}
    </Loading>
  );
}

/** Placeholder cells for the label/value grid on the detail screens. */
export function FactSkeletons({ count = 8 }: { count?: number }) {
  return (
    <Loading className="facts">
      {Array.from({ length: count }, (_, index) => (
        <div className="fact" key={index}>
          <Skeleton w="45%" h="0.65rem" />
          <span className="fact__value">
            <Skeleton w={jitter(index, 55, 35)} h="1rem" />
          </span>
        </div>
      ))}
    </Loading>
  );
}


export function Meter({
  value,
  tone = "ok",
  label,
}: {
  value: number;
  tone?: Tone;
  label: string;
}) {
  const clamped = Math.min(1, Math.max(0, value));
  return (
    <span className="meter" data-tone={tone} title={label}>
      <span className="meter__fill" style={{ scale: `${clamped} 1` }} />
    </span>
  );
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="fact">
      <span className="fact__label">{label}</span>
      <span className="fact__value">{children}</span>
    </div>
  );
}
