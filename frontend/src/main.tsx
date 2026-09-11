import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { ApiError } from "./lib/api";
import { AuthProvider } from "./lib/auth";

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/app.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // A 401 is already handled by the client's refresh-and-retry, and a 404
      // will not become a 200 on the second ask.
      retry: (attempt, error) =>
        !(error instanceof ApiError && [401, 403, 404].includes(error.status)) &&
        attempt < 2,
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
