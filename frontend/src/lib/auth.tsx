/** Who is signed in, for the rest of the app to read.
 *
 * There is no token in the browser to inspect: the pair lives in httpOnly
 * cookies. So the only way to know whether anyone is here is to ask the API,
 * and a 401 is the answer "nobody".
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "./api";
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
  const queryClient = useQueryClient();

  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 401) && count < 2,
    staleTime: 5 * 60 * 1000,
  });

  const [signingOut, setSigningOut] = useState(false);

  const signOut = useCallback(async () => {
    setSigningOut(true);
    try {
      // A failure here only means the server-side session outlives the
      // client, so it must not stop us from clearing this end.
      await api.logout().catch(() => undefined);
      queryClient.clear();
    } finally {
      setSigningOut(false);
    }
  }, [queryClient]);

  const value = useMemo<AuthValue>(() => {
    let status: Status;
    if (me.isPending) status = "loading";
    else if (me.data) status = "signed-in";
    else if (me.error instanceof ApiError && me.error.status === 401) {
      status = "anonymous";
    } else status = me.error ? "error" : "anonymous";

    return {
      status,
      user: me.data ?? null,
      error: me.error instanceof Error ? me.error.message : null,
      signOut,
      signingOut,
    };
  }, [me.isPending, me.data, me.error, signOut, signingOut]);

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth used outside AuthProvider");
  return value;
}
