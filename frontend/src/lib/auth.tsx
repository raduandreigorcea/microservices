/** Who is signed in, for the rest of the app to read. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "./api";
import { getTokens, setTokens, subscribe } from "./session";
import type { User } from "./types";

type Status = "anonymous" | "loading" | "signed-in" | "error";

interface AuthValue {
  status: Status;
  user: User | null;
  error: string | null;
  signOut: () => Promise<void>;
  /** True while the logout round-trip is in flight. */
  signingOut: boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const tokens = useSyncExternalStore(subscribe, getTokens, getTokens);
  const queryClient = useQueryClient();

  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    enabled: Boolean(tokens?.access),
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 401) && count < 2,
    staleTime: 5 * 60 * 1000,
  });

  const [signingOut, setSigningOut] = useState(false);

  const signOut = useCallback(async () => {
    setSigningOut(true);
    try {
      const current = getTokens();
      if (current?.refresh) {
        // A failure here only means the server-side session outlives the
        // client, so it must not stop us from clearing this end.
        await api.logout(current.refresh).catch(() => undefined);
      }
      setTokens(null);
      queryClient.clear();
    } finally {
      setSigningOut(false);
    }
  }, [queryClient]);

  const value = useMemo<AuthValue>(() => {
    let status: Status = "anonymous";
    if (tokens?.access) {
      if (me.isPending) status = "loading";
      else if (me.data) status = "signed-in";
      else status = "error";
    }
    return {
      status,
      user: me.data ?? null,
      error: me.error instanceof Error ? me.error.message : null,
      signOut,
      signingOut,
    };
  }, [tokens, me.isPending, me.data, me.error, signOut, signingOut]);

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth used outside AuthProvider");
  return value;
}

/**
 * user_service hands the token pair back in the URL fragment. Read it once,
 * store it, and scrub it out of the address bar before anything can log it.
 */
export function useHashTokens(): boolean {
  const [claimed, setClaimed] = useState(false);

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) {
      setClaimed(true);
      return;
    }
    const params = new URLSearchParams(hash);
    const access = params.get("access_token");
    const refresh = params.get("refresh_token");
    if (access && refresh) {
      setTokens({
        access,
        refresh,
        expiresAt: Date.now() + Number(params.get("expires_in") ?? 900) * 1000,
      });
    }
    history.replaceState(null, "", window.location.pathname);
    setClaimed(true);
  }, []);

  return claimed;
}
