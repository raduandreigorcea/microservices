/** Numbers and dates, in Romanian, formatted once and reused. */

const locale = "ro-MD";

const integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat(locale, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const compact = new Intl.NumberFormat(locale, {
  notation: "compact",
  maximumFractionDigits: 1,
});
const dayMonthYear = new Intl.DateTimeFormat(locale, {
  day: "2-digit",
  month: "short",
  year: "numeric",
});
const withTime = new Intl.DateTimeFormat(locale, {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

export const DASH = "—";

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : integer.format(value);
}

export function short(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return Math.abs(value) < 10_000 ? integer.format(value) : compact.format(value);
}

/** Money arrives as a decimal string, so parse before formatting. */
export function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return DASH;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? decimal.format(parsed) : DASH;
}

export function date(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? DASH : dayMonthYear.format(parsed);
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? DASH : withTime.format(parsed);
}

const STEPS: [limit: number, divisor: number, unit: Intl.RelativeTimeFormatUnit][] =
  [
    [60, 1, "second"],
    [3600, 60, "minute"],
    [86_400, 3600, "hour"],
    [604_800, 86_400, "day"],
    [2_629_800, 604_800, "week"],
    [31_557_600, 2_629_800, "month"],
  ];

export function ago(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return DASH;

  const seconds = (parsed.getTime() - Date.now()) / 1000;
  const size = Math.abs(seconds);
  for (const [limit, divisor, unit] of STEPS) {
    if (size < limit) return relative.format(Math.round(seconds / divisor), unit);
  }
  return relative.format(Math.round(seconds / 31_557_600), "year");
}

/** Splits pasted text into candidate IDNOs on anything that is not a digit. */
export function parseIdnos(raw: string): string[] {
  const seen = new Set<string>();
  for (const chunk of raw.split(/[^0-9]+/)) {
    if (chunk) seen.add(chunk);
  }
  return [...seen];
}

export const isIdno = (value: string): boolean => /^\d{13}$/.test(value);

export function duration(from: string | null, to: string | null): string {
  if (!from) return DASH;
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return DASH;

  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
