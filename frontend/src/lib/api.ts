/** The only place that talks to the gateway.
 *
 * Every call carries the bearer token. A 401 buys exactly one refresh attempt,
 * shared between concurrent callers, and then one retry. If that fails the
 * session is dropped and the app falls back to the gate.
 */

import { fromPair, getTokens, setTokens } from "./session";
import type {
  Company,
  CompanyPage,
  Graph,
  Job,
  JobStatus,
  SourceData,
  Statement,
  TokenPair,
  User,
} from "./types";

/** The dev server proxies this prefix to the gateway. */
export const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI puts the useful part in `detail`, which is a string or a list. */
function messageFrom(body: unknown, fallback: string): string {
  if (typeof body === "string" && body) return body;
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string } | undefined;
      if (first?.msg) return first.msg;
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
  }
  return fallback;
}

async function parse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

let refreshing: Promise<boolean> | null = null;

/** Trades the refresh token for a new pair. At most one runs at a time. */
async function refreshSession(): Promise<boolean> {
  if (refreshing) return refreshing;

  refreshing = (async () => {
    const tokens = getTokens();
    if (!tokens?.refresh) return false;
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: tokens.refresh }),
      });
      if (!response.ok) return false;
      setTokens(fromPair((await response.json()) as TokenPair));
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();

  return refreshing;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Set on the retry so a failed refresh cannot loop. */
  retried?: boolean;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, signal, query, retried = false } = options;

  let url = `${API_BASE}${path}`;
  if (query) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    const qs = search.toString();
    if (qs) url += `?${qs}`;
  }

  const headers: Record<string, string> = { accept: "application/json" };
  const tokens = getTokens();
  if (tokens?.access) headers.authorization = `Bearer ${tokens.access}`;
  if (body !== undefined) headers["content-type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, "gateway-ul nu răspunde");
  }

  if (response.status === 401 && !retried) {
    if (await refreshSession()) {
      return request<T>(path, { ...options, retried: true });
    }
    setTokens(null);
  }

  if (!response.ok) {
    const parsed = await parse(response);
    throw new ApiError(
      response.status,
      messageFrom(parsed, `${response.status} ${response.statusText}`),
      parsed,
    );
  }

  return (await parse(response)) as T;
}

// --- endpoints --------------------------------------------------------------

export const api = {
  me: () => request<User>("/users/me"),

  logout: (refreshToken: string) =>
    request<null>("/auth/logout", {
      method: "POST",
      body: { refresh_token: refreshToken },
    }),

  companies: (params: { search?: string; limit?: number; offset?: number }) =>
    request<CompanyPage>("/companies", { query: params }),

  company: (idno: string) => request<Company>(`/companies/${idno}`),

  /** One company's neighbourhood: its parties, and where else they sit. */
  companyGraph: (idno: string) => request<Graph>(`/companies/${idno}/graph`),

  statement: (idno: string, year: number, source?: string) =>
    request<Statement>(`/companies/${idno}/statements/${year}`, {
      query: { source },
    }),

  sourceData: (idno: string, limit = 50, offset = 0) =>
    request<SourceData[]>(`/companies/${idno}/source-data`, {
      query: { limit, offset },
    }),

  jobs: (params: { limit?: number; offset?: number; status?: JobStatus }) =>
    request<Job[]>("/scrape/jobs", { query: params }),

  job: (id: string) => request<Job>(`/scrape/jobs/${id}`),

  cancelJob: (id: string) =>
    request<Job>(`/scrape/jobs/${id}/cancel`, { method: "POST" }),

  scrapeByIdno: (idnos: string[], includeDepozitar?: boolean) =>
    request<Job>("/scrape/companies", {
      method: "POST",
      body: { idnos, include_depozitar: includeDepozitar },
    }),

  sweep: (payload: {
    start_page: number;
    page_size: number;
    max_pages: number;
    include_depozitar?: boolean;
  }) => request<Job>("/scrape/sweep", { method: "POST", body: payload }),
};

/** Sends the browser to Google. The gateway passes /auth straight through. */
export function startGoogleLogin(): void {
  window.location.href = `${API_BASE}/auth/google/login`;
}
