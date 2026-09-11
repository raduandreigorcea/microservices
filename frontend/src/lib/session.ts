/** Where the token pair lives, and who gets told when it changes.
 *
 * localStorage rather than memory so a reload does not throw the user back to
 * the gate, and a tiny subscription so React re-renders when it moves. Every
 * access is wrapped: private windows and blocked site data make it throw.
 */

const KEY = "registru.tokens";

export interface StoredTokens {
  access: string;
  refresh: string;
  /** Epoch milliseconds. A rough guide, not a guarantee. */
  expiresAt: number;
}

type Listener = (tokens: StoredTokens | null) => void;

const listeners = new Set<Listener>();
let cached: StoredTokens | null | undefined;

function read(): StoredTokens | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredTokens>;
    if (typeof parsed.access !== "string" || typeof parsed.refresh !== "string") {
      return null;
    }
    return {
      access: parsed.access,
      refresh: parsed.refresh,
      expiresAt: typeof parsed.expiresAt === "number" ? parsed.expiresAt : 0,
    };
  } catch {
    return null;
  }
}

export function getTokens(): StoredTokens | null {
  if (cached === undefined) cached = read();
  return cached;
}

export function setTokens(tokens: StoredTokens | null): void {
  cached = tokens;
  try {
    if (tokens) localStorage.setItem(KEY, JSON.stringify(tokens));
    else localStorage.removeItem(KEY);
  } catch {
    // Session still works for this tab; it just will not survive a reload.
  }
  for (const listener of listeners) listener(tokens);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Turns the /auth/refresh body into something storable. */
export function fromPair(pair: {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}): StoredTokens {
  return {
    access: pair.access_token,
    refresh: pair.refresh_token,
    expiresAt: Date.now() + pair.expires_in * 1000,
  };
}
